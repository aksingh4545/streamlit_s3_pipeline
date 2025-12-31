#NER model

import os
import re
import csv
import logging
from typing import List, Union
from datetime import datetime
import pdfplumber
import spacy
import mysql.connector
import boto3
from docx import Document
from dotenv import load_dotenv


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "resume-input-pdfs")

MYSQL_CONFIG = {
    "host": os.getenv("MYSQL_HOST"),
    "user": os.getenv("MYSQL_USER"),
    "password": os.getenv("MYSQL_PASSWORD"),
    "database": os.getenv("MYSQL_DB"),
}

s3 = boto3.client("s3")
nlp = spacy.load("en_core_web_sm")

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
    
    ext = os.path.splitext(path)[1].lower()

    if ext == ".pdf":
        return read_pdf(path)
    if ext == ".docx":
        return read_docx(path)
    if ext == ".txt":
        return read_txt(path)

    logger.warning(f"Unknown file type {ext}. Attempting raw text read.")
    return read_txt(path)


def extract_email(text):
    m = re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
    return m.group(0) if m else None


def extract_phone(text):
    mobile_indicators = [
        r"(?:Mobile|Phone|Contact|Tel|Telephone|mobile:|phone:|contact:|tel:)\s*[:\-]?\s*([+]?[0-9\s\-\(\)]{10,15})",
        r"(?:Mobile No|Phone No|Contact No)\s*[:\-]?\s*([+]?[0-9\s\-\(\)]{10,15})",
        r"(?:Mobile Number|Phone Number)\s*[:\-]?\s*([+]?[0-9\s\-\(\)]{10,15})"
    ]

    for pattern in mobile_indicators:
        match = re.search(pattern, text, re.I)
        if match:
            phone = match.group(1)
            
            digits = re.sub(r"\D", "", phone)
            if len(digits) > 10:
                digits = digits[-10:]  
            if len(digits) == 10 and digits[0] in "6789":
                return digits

    
    text_cleaned = re.sub(r"[^\d+]", " ", text)
    candidates = re.findall(r"\+?\d{10,13}", text_cleaned)

    for c in candidates:
        digits = re.sub(r"\D", "", c)
        if len(digits) > 10:
            digits = digits[-10:]
        if len(digits) == 10 and digits[0] in "6789":
            return digits

    return None


def extract_dob(text):
    
    dob_indicators = [
        r"(?:DOB|Date of Birth|Birth Date|Date of birth)[::\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{4})",
        r"(?:DOB|Date of Birth|Birth Date|Date of birth)[::\s]+(\d{1,2}\s+[A-Za-z]{3,9},?\s*\d{4})",
        r"(?:DOB|Date of Birth|Birth Date|Date of birth)\s+(\d{1,2}[/-]\d{1,2}[/-]\d{4})",
        r"(?:DOB|Date of Birth|Birth Date|Date of birth)\s+(\d{1,2}\s+[A-Za-z]{3,9},?\s*\d{4})"
    ]

    for pattern in dob_indicators:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1)

    general_patterns = [
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b",
        r"\b\d{1,2}\s+[A-Za-z]{3,9},?\s*\d{4}\b",
    ]

    
    context_patterns = [
        r"(?:DOB|Date of Birth|Birth Date|date of birth|birth|DOB:|Date:|Born|Born on)\W*(\d{1,2}[/-]\d{1,2}[/-]\d{4})",
        r"(?:DOB|Date of Birth|Birth Date|date of birth|birth|DOB:|Date:|Born|Born on)\W*(\d{1,2}\s+[A-Za-z]{3,9},?\s*\d{4})"
    ]

    for pattern in context_patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1)

   
    for pattern in general_patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(0)

    return None


def normalize_dob(dob_raw):
    if not dob_raw:
        return None

    dob_raw = dob_raw.replace('"', '').strip()
    dob_raw = re.sub(r"\s+", " ", dob_raw)

    formats = [
        "%d %B %Y",     # 19 November 2004
        "%d %b %Y",     # 29 Oct 2003
        "%d %b, %Y",    # 29 Oct, 2003
        "%d/%m/%Y",
        "%d-%m-%Y"
    ]

    for fmt in formats:
        try:
            return datetime.strptime(dob_raw, fmt).date().isoformat()
        except ValueError:
            continue

    return None

def extract_gender(text):
    
    gender_indicators = [
        r"(?:Gender:|Sex:|Male/Female:|gender:|sex:)\s*(Male|Female|Other|M|F)",
        r"(?:Gender|Sex)\s*[:\-]\s*(Male|Female|Other|M|F)",
        r"(?:Personal Info|Profile|Basic Info).*?(Male|Female|Other|M|F)"
    ]

    for pattern in gender_indicators:
        match = re.search(pattern, text, re.I)
        if match:
            g = match.group(1).lower()
            return "Male" if g in ("m", "male") else "Female" if g in ("f", "female") else "Other"

  
    pattern = r"\b(Male|Female|Other|M|F)\b"
    matches = list(re.finditer(pattern, text, re.I))
    if matches:
        
        first_match = matches[0]  
        g = first_match.group(1).lower()
        return "Male" if g in ("m", "male") else "Female" if g in ("f", "female") else "Other"

    return None


def extract_name(text, email=None):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    top = lines[:10]  # slightly wider window

    ignore = {
        "b.tech","m.tech","b.e","m.e","mba","msc","bsc",
        "engineering","technology","computer",
        "resume","curriculum vitae","cv",
        "student","intern","engineer","developer","profile",
        "electronics","communication","brief","summary",
        "data","scientist","software","manager","director",
        "analyst","consultant","architect","lead","senior",
        "junior","fresher","experience","years","year",
        "company", "organization", "inc", "llc", "ltd"
    }

    name_indicators = {
        "name","full name","candidate name",
        "first name","last name","surname","given name"
    }

    def clean(line):
        return re.sub(r"[^A-Za-z.\s]", " ", line).strip()

    def looks_like_name(words):
        return (
            2 <= len(words) <= 4 and
            not any(w.lower() in ignore for w in words)
        )

    # 1. Try explicit name lines
    for line in top:
        lower = line.lower()

        # Check if line contains name indicators and extract the actual name
        for key in name_indicators:
            if key in lower:
                name_part = re.sub(
                    rf"(?i).*{re.escape(key)}\s*[:\-]*\s*",
                    "",
                    line
                )

                c = clean(name_part)
                words = c.split()

                if looks_like_name(words):
                    
                    if c.replace(".", "").isupper():
                        return c.title()
                    return c

        
        c = clean(line)
        words = c.split()

        if looks_like_name(words):
            
            if not any(w.lower() in ignore for w in words):
                
                if c.replace(".", "").isupper():
                    return c.title()
                return c

    
    doc = nlp(" ".join(top))
    for ent in doc.ents:
        if ent.label_ == "PERSON":
            c = clean(ent.text)
            words = c.split()
            if looks_like_name(words):
                
                if c.replace(".", "").isupper():
                    return c.title()
                return c

  
    if email:
        username = email.split("@")[0]
        username = re.sub(r"\d+", "", username)
        parts = re.split(r"[._]", username)
        if 1 < len(parts) <= 3:
            return " ".join(p.capitalize() for p in parts)

    return None
DEFAULT_DOB = "NA"
def extract_entities(text):
    raw_dob = extract_dob(text)
    dob = normalize_dob(raw_dob) or DEFAULT_DOB
    return {
        "name": extract_name(text),
        "email": extract_email(text),
        "mobile": extract_phone(text),
        "dob": dob, 
        "gender": extract_gender(text) or "Not Specified",  # Default value for Gender
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
        writer = csv.writer(
            f,
            quoting=csv.QUOTE_MINIMAL  # 🔑 THIS is mandatory
        )

        writer.writerow([
            row["id"],
            row["name"],
            row["email"],
            row["mobile"],
            row["dob"],      # "29 Oct, 2003" will be auto-quoted
            row["gender"]
        ])

    s3.upload_file(filename, BUCKET_NAME, f"processed/{filename}")
    os.remove(filename)

    logger.info(f"Uploaded CSV to S3: processed/{filename}")

def run_pipeline(files: Union[str, List[str]]):
  
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
