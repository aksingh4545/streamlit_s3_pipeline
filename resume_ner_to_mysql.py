import os
import re
import csv
import logging
from typing import List, Union

import pdfplumber
import spacy
import mysql.connector
import boto3
from docx import Document
from dotenv import load_dotenv

# --------------------------------------------------
# ENV + LOGGING
# --------------------------------------------------
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# --------------------------------------------------
# CONFIG
# --------------------------------------------------
BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "resume-input-pdfs")

MYSQL_CONFIG = {
    "host": os.getenv("MYSQL_HOST"),
    "user": os.getenv("MYSQL_USER"),
    "password": os.getenv("MYSQL_PASSWORD"),
    "database": os.getenv("MYSQL_DB"),
}

s3 = boto3.client("s3")
nlp = spacy.load("en_core_web_sm")

# --------------------------------------------------
# FILE READERS
# --------------------------------------------------
def read_pdf(path: str) -> str:
    text = ""
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
    except Exception as e:
        logger.error(f"PDF read failed: {e}")
    return text.strip()


def read_docx(path: str) -> str:
    try:
        doc = Document(path)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception as e:
        logger.error(f"DOCX read failed: {e}")
        return ""


def read_txt(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception as e:
        logger.error(f"TXT read failed: {e}")
        return ""


def read_any_file(path: str) -> str:
    """
    Reads known formats properly.
    Unknown formats are attempted as text.
    """
    ext = os.path.splitext(path)[1].lower()

    if ext == ".pdf":
        return read_pdf(path)
    if ext == ".docx":
        return read_docx(path)
    if ext == ".txt":
        return read_txt(path)

    logger.warning(f"Unknown file type {ext}. Attempting raw text read.")
    return read_txt(path)

# --------------------------------------------------
# EXTRACTION HELPERS
# --------------------------------------------------
def extract_email(text):
    m = re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
    return m.group(0) if m else None


def extract_phone(text):
    text = re.sub(r"[^\d+]", " ", text)
    candidates = re.findall(r"\+?\d{10,13}", text)

    for c in candidates:
        digits = re.sub(r"\D", "", c)
        if len(digits) > 10:
            digits = digits[-10:]
        if len(digits) == 10 and digits[0] in "6789":
            return digits
    return None


def extract_dob(text):
    patterns = [
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b",
        r"\b\d{1,2}\s+[A-Za-z]{3,9},?\s*\d{4}\b",
    ]
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            return m.group(0)
    return None


def extract_gender(text):
    m = re.search(r"\b(Male|Female|Other|M|F)\b", text, re.I)
    if not m:
        return None
    g = m.group(1).lower()
    return "Male" if g in ("m", "male") else "Female" if g in ("f", "female") else "Other"


def extract_name(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    top = lines[:8]

    ignore = [
        "b.tech", "m.tech", "b.e", "m.e", "mba", "msc", "bsc",
        "engineering", "technology", "computer",
        "resume", "curriculum vitae", "cv",
        "student", "intern", "engineer", "developer", "profile",
        "electronics", "communication","Brief","Summary"
    ]

    def noisy(line):
        return any(w in line.lower() for w in ignore)

    def clean(line):
        return re.sub(r"[^A-Za-z.\s]", " ", line).strip()

    for line in top:
        if noisy(line):
            continue

        c = clean(line)
        words = c.split()

        if 2 <= len(words) <= 5:
            if c.replace(".", "").isupper():
                return c.title()
            if all(w[0].isupper() for w in words if len(w) > 1):
                return c

    doc = nlp(" ".join(top))
    for ent in doc.ents:
        if ent.label_ == "PERSON":
            name = clean(ent.text)
            if 2 <= len(name.split()) <= 5:
                return name

    return None

# --------------------------------------------------
# ENTITY PIPELINE
# --------------------------------------------------
def extract_entities(text):
    return {
        "name": extract_name(text),
        "email": extract_email(text),
        "mobile": extract_phone(text),
        "dob": extract_dob(text),
        "gender": extract_gender(text),
    }

# --------------------------------------------------
# MYSQL + S3
# --------------------------------------------------
def save_and_fetch_mysql(entity):
    conn = mysql.connector.connect(**MYSQL_CONFIG)
    cursor = conn.cursor(dictionary=True, buffered=True)

    cursor.execute(
        """
        INSERT INTO resume_entities (name, email, mobile, dob, gender)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            name = VALUES(name),
            mobile = VALUES(mobile),
            dob = VALUES(dob),
            gender = VALUES(gender)
        """,
        (
            entity["name"],
            entity["email"],
            entity["mobile"],
            entity["dob"],
            entity["gender"],
        ),
    )

    conn.commit()

    cursor.execute(
        "SELECT * FROM resume_entities WHERE email = %s",
        (entity["email"],)
    )

    row = cursor.fetchone()

    cursor.close()
    conn.close()
    return row


def save_csv_to_s3(row):
    filename = f"{row['id']}.csv"

    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(row.keys())
        writer.writerow(row.values())

    s3.upload_file(filename, BUCKET_NAME, f"processed/{filename}")
    os.remove(filename)

    logger.info(f"Uploaded CSV to S3: processed/{filename}")

# --------------------------------------------------
# MAIN ENTRY (SINGLE OR MULTIPLE FILES)
# --------------------------------------------------
def run_pipeline(files: Union[str, List[str]]):
    """
    Accepts:
    - single file path
    - list of file paths
    """
    if isinstance(files, str):
        files = [files]

    results = []

    for file_path in files:
        logger.info(f"Processing: {file_path}")

        try:
            text = read_any_file(file_path)

            if not text or len(text.strip()) < 30:
                raise ValueError("No readable text found")

            entity = extract_entities(text)
            logger.info(f"Extracted: {entity}")

            row = save_and_fetch_mysql(entity)
            save_csv_to_s3(row)

            results.append({
                "file": os.path.basename(file_path),
                "status": "success",
                "data": entity
            })

        except Exception as e:
            logger.error(f"Failed processing {file_path}: {e}")
            results.append({
                "file": os.path.basename(file_path),
                "status": "failed",
                "error": str(e)
            })

    return results
