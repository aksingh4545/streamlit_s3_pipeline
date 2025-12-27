import os
import re
import csv
import logging

import pdfplumber
import spacy
import mysql.connector
import boto3
from docx import Document
from dotenv import load_dotenv


load_dotenv()

logging.basicConfig(level=logging.INFO)
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

SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".txt")

s3 = boto3.client("s3")
nlp = spacy.load("en_core_web_sm")
def read_pdf(path):
    text = ""
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
    except Exception as e:
        logger.error(f"PDF read error: {e}")
    return text.strip()


def read_docx(path):
    doc = Document(path)
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def read_txt(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def read_file(path):
    ext = os.path.splitext(path)[1].lower()

    if ext == ".pdf":
        return read_pdf(path)
    if ext == ".docx":
        return read_docx(path)
    if ext == ".txt":
        return read_txt(path)

    raise ValueError(f"Unsupported file type: {ext}")

    

def extract_email(text):
    m = re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
    return m.group(0) if m else None

        
def extract_phone(text):
    m = re.search(r"\+?\d{10,13}", text)
    return m.group(0) if m else None


def extract_dob(text):
    patterns = [
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b",
        r"\b\d{1,2}\s+[A-Za-z]{3,9},?\s*\d{4}\b",
        r"\b(?:DOB|Date\s*of\s*Birth)\s*[:\-]?\s*\d{1,2}\s+[A-Za-z]{3,9},?\s*\d{4}\b",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return re.sub(
                r"^(DOB|Date\s*of\s*Birth)\s*[:\-]?\s*",
                "",
                m.group(0),
                flags=re.I
            )
    return None


def extract_gender(text):
    m = re.search(r"\b(Male|Female|Other|M|F)\b", text, re.I)
    if not m:
        return None
    g = m.group(1).lower()
    return "Male" if g in ("m", "male") else "Female" if g in ("f", "female") else "Other"


def extract_name(text):
  

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    top_lines = lines[:8]

    
    ignore_words = [
        "b.tech", "m.tech", "b.e", "m.e", "mba", "msc", "bsc",
        "engineering", "technology", "computer",
        "resume", "curriculum vitae", "cv",
        "student", "intern", "engineer", "developer", "profile",
        "electronics", "communication","Brief","Summary"
    ]

    def is_noise(line):
        l = line.lower()
        return any(w in l for w in ignore_words)

    def clean_name(line):
        return re.sub(r"[^A-Za-z.\s]", " ", line).strip()

   
    for line in top_lines:
        m = re.search(r"\bname\s*[:\-]\s*(.+)", line, re.I)
        if m:
            candidate = clean_name(m.group(1))
            words = candidate.split()
            if 2 <= len(words) <= 5:
                return candidate.title()

    
    for line in top_lines:
        if is_noise(line):
            continue

        clean = clean_name(line)
        words = clean.split()

        if not (2 <= len(words) <= 5):
            continue

        if clean.replace(".", "").isupper():
            return clean.title()

        if all(w[0].isupper() for w in words if len(w) > 1):
            return clean

   
    doc = nlp(" ".join(top_lines))
    for ent in doc.ents:
        if ent.label_ == "PERSON":
            name = clean_name(ent.text)
            if 2 <= len(name.split()) <= 5:
                return name

    return None



def extract_entities(raw_text):
    return {
        "name": extract_name(raw_text),
        "email": extract_email(raw_text),
        "mobile": extract_phone(raw_text),
        "dob": extract_dob(raw_text),
        "gender": extract_gender(raw_text),
    }


def save_and_fetch_mysql(entity):
    conn = mysql.connector.connect(**MYSQL_CONFIG)
    cursor = conn.cursor(dictionary=True)

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
        """
        SELECT id, name, email, mobile, dob, gender
        FROM resume_entities
        WHERE email = %s
        """,
        (entity["email"],)
    )

    rows = cursor.fetchall()
    row = rows[0] if rows else None

    cursor.close()
    conn.close()

    return row


def save_csv_to_s3(row):
    filename = f"{row['id']}.csv"

    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "name", "email", "mobile", "dob", "gender"])
        writer.writerow([
            row["id"],
            row["name"],
            row["email"],
            row["mobile"],
            row["dob"],
            row["gender"],
        ])

    s3.upload_file(
        filename,
        BUCKET_NAME,
        f"processed/{filename}"
    )

    os.remove(filename)
    logger.info(f"CSV uploaded to S3: processed/{filename}")


def run_pipeline(local_file):
    raw_text = read_file(local_file)

    if not raw_text or len(raw_text.strip()) < 30:
        raise ValueError(
            "This file appears to be scanned or contains no readable text."
        )

    entity = extract_entities(raw_text)
    logger.info(f"Extracted: {entity}")

    row = save_and_fetch_mysql(entity)
    save_csv_to_s3(row)

    return entity
