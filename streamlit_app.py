import streamlit as st
import os
import tempfile
import boto3
from dotenv import load_dotenv
from resume_ner_to_mysql import run_pipeline

# --------------------------------------------------
# ENV + AWS
# --------------------------------------------------
load_dotenv()

BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "resume-input-pdfs")
SUPPORTED_EXTENSIONS = ["pdf", "docx", "txt"]

s3 = boto3.client("s3")

# --------------------------------------------------
# STREAMLIT UI
# --------------------------------------------------
st.set_page_config(
    page_title="Resume NER Processing",
    layout="centered"
)

st.title("Resume NER Processing")
st.write(
    "Upload a resume (PDF, DOCX, or TXT). "
    "The system extracts entities, stores them in MySQL, "
    "and pushes CSV data to S3 for Snowflake ingestion."
)

uploaded_file = st.file_uploader(
    "Upload Resume",
    type=SUPPORTED_EXTENSIONS
)

if uploaded_file:
    st.success(f"Uploaded: {uploaded_file.name}")

    file_ext = uploaded_file.name.split(".")[-1].lower()

    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_ext}") as tmp:
        tmp.write(uploaded_file.read())
        temp_file_path = tmp.name

    st.info("Processing resume...")

    try:
        extracted_data = run_pipeline(temp_file_path)

        # Upload original file (optional, for audit/debug)
        s3.upload_file(
            temp_file_path,
            BUCKET_NAME,
            f"uploaded/{uploaded_file.name}"
        )

        st.success("Resume processed successfully")

        st.subheader("Extracted Entities")
        st.json({
            "Name": extracted_data.get("name"),
            "Email": extracted_data.get("email"),
            "Mobile": extracted_data.get("mobile"),
            "DOB": extracted_data.get("dob"),
            "Gender": extracted_data.get("gender"),
        })

    except Exception as e:
        st.error(str(e))
        st.info(
            "If this is a scanned PDF, please upload a DOCX "
            "or a text-based PDF."
        )

    finally:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
