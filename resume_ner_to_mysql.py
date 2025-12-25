import os
import re
import json
import logging
from datetime import datetime
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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --------------------------------------------------
# CONFIG
# --------------------------------------------------
BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "resume-input-pdfs")

MYSQL_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD"),
    "database": os.getenv("MYSQL_DB", "resume_db"),
}

SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".txt")

s3 = boto3.client("s3")
nlp = spacy.load("en_core_web_sm")

# --------------------------------------------------
# FILE READERS
# --------------------------------------------------
def read_pdf(path):
    text = ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            if page.extract_text():
                text += page.extract_text() + "\n"
    return text


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
        # DD/MM/YYYY or DD-MM-YYYY
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b",

        # DD Month YYYY  (01 Jan 2006)
        r"\b\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}\b",

        # DD Month, YYYY (01 Jan, 2006)
        r"\b\d{1,2}\s+[A-Za-z]{3,9},\s*\d{4}\b",

        # With labels: DOB / Date of Birth
        r"\b(?:DOB|Date\s*of\s*Birth)\s*[:\-]?\s*\d{1,2}\s+[A-Za-z]{3,9},?\s*\d{4}\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            # Return only the date part (remove label if present)
            return re.sub(
                r"^(?:DOB|Date\s*of\s*Birth)\s*[:\-]?\s*",
                "",
                match.group(0),
                flags=re.IGNORECASE
            ).strip()

    return None


def extract_gender(text):
    m = re.search(r"\b(Male|Female|Other|M|F)\b", text, re.I)
    if not m:
        return None
    g = m.group(1).lower()
    return "Male" if g in ("m", "male") else "Female" if g in ("f", "female") else "Other"

def extract_name(text):
    """
    Robust resume name extractor.
    Strategy:
    1. Look at top lines only
    2. Remove degree/designation noise
    3. Prefer ALL CAPS / Title Case names
    4. Fallback to spaCy PERSON entities
    """

    # --- Step 1: get top meaningful lines ---
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    top_lines = lines[:6]  # names are almost always at the top

    # --- Step 2: common words to ignore ---
    ignore_words = [
        "b.tech", "m.tech", "b.e", "m.e", "mba", "msc", "bsc",
        "electronics", "communication", "engineering",
        "computer", "science", "technology",
        "resume", "curriculum vitae", "cv"
    ]

    def is_noise(line):
        l = line.lower()
        return any(word in l for word in ignore_words)

    # --- Step 3: regex-based name detection ---
    for line in top_lines:
        if is_noise(line):
            continue

        # Remove extra symbols
        clean = re.sub(r"[^A-Za-z\s]", " ", line).strip()
        words = clean.split()

        # ALL CAPS name (ARCHANA BHAGAT)
        if clean.isupper() and 2 <= len(words) <= 4:
            return clean.title()

        # Title Case name (Archana Bhagat)
        if all(w[0].isupper() for w in words if len(w) > 1) and 2 <= len(words) <= 4:
            return clean

    # --- Step 4: spaCy fallback ---
    doc = nlp(" ".join(top_lines))
    for ent in doc.ents:
        if ent.label_ == "PERSON":
            name = ent.text.strip()
            if 2 <= len(name.split()) <= 4:
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

# --------------------------------------------------
# MYSQL STORAGE (OPTIONAL)
# --------------------------------------------------
def save_to_mysql(entity):
    conn = mysql.connector.connect(**MYSQL_CONFIG)
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO resume_entities (name, email, mobile, dob, gender)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            name = VALUES(name),
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
    cursor.close()
    conn.close()

    logger.info("Saved record to MySQL")

# --------------------------------------------------
# S3 JSON STORAGE (FOR SNOWFLAKE)
# --------------------------------------------------
def save_json_to_s3(entity, source_filename):
    payload = {
        **entity,
        "source_file": source_filename,
        "ingested_at": datetime.utcnow().isoformat()
    }

    json_key = f"processed/{source_filename}.json"

    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=json_key,
        Body=json.dumps(payload),
        ContentType="application/json"
    )

    logger.info(f"JSON written to S3: {json_key}")

# -------------------ssssssssssss-------------------------------
# MAIN PIPELINE
# --------------------------------------------------
def run_pipeline(local_file):
    raw_text = read_file(local_file)

    if not raw_text.strip():
        raise ValueError("Empty or unreadable file")

    entity = extract_entities(raw_text)
    logger.info(f"Extracted: {entity}")

    save_to_mysql(entity)
    save_json_to_s3(entity, os.path.basename(local_file))

    return entity

# --------------------------------------------------
# S3 BATCH PROCESS (PDF → JSON)
# --------------------------------------------------
def process_s3_files():
    response = s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix="incoming/")

    if "Contents" not in response:
        logger.info("No incoming files found")
        return

    for obj in response["Contents"]:
        key = obj["Key"]
        filename = os.path.basename(key)

        if not filename.lower().endswith(SUPPORTED_EXTENSIONS):
            continue

        logger.info(f"Processing {filename}")
        s3.download_file(BUCKET_NAME, key, filename)

        try:
            run_pipeline(filename)

            s3.delete_object(Bucket=BUCKET_NAME, Key=key)
            logger.info(f"Processed and removed {filename}")

        except Exception as e:
            logger.error(f"Failed processing {filename}: {e}")
            s3.copy_object(
                Bucket=BUCKET_NAME,
                CopySource={"Bucket": BUCKET_NAME, "Key": key},
                Key=f"failed/{filename}",
            )
            s3.delete_object(Bucket=BUCKET_NAME, Key=key)

        finally:
            if os.path.exists(filename):
                os.remove(filename)

# --------------------------------------------------
# ENTRY
# --------------------------------------------------
if __name__ == "__main__":
    process_s3_files()
