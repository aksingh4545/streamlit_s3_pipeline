import streamlit as st
import os
import tempfile
import boto3
from resume_ner_to_mysql import run_pipeline

# ------------------ CONFIG ------------------
BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "resume-input-pdfs")
SUPPORTED_EXTENSIONS = ["pdf", "docx", "txt"]

s3 = boto3.client("s3")

# ------------------ UI ------------------
st.set_page_config(page_title="Resume NER Pipeline", layout="centered")

st.title("Resume NER Processing")
st.write(
    "Upload a resume (PDF, DOCX, or TXT). "
    "The system will extract entities and store them in MySQL and S3."
)

uploaded_file = st.file_uploader(
    "Upload Resume",
    type=SUPPORTED_EXTENSIONS
)

if uploaded_file:
    st.success(f"Uploaded: {uploaded_file.name}")

    file_ext = uploaded_file.name.split(".")[-1]

    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_ext}") as tmp:
        tmp.write(uploaded_file.read())
        temp_file_path = tmp.name

    st.info("Processing resume...")

    try:
        # ---- Run full pipeline ----
        extracted_data = run_pipeline(temp_file_path)

        # ---- Upload original file to S3 ----
        s3.upload_file(
            temp_file_path,
            BUCKET_NAME,
            f"uploaded/{uploaded_file.name}"
        )

        st.success("Resume processed successfully")

        # ---- Display extracted data ----
        st.subheader("Extracted Entities")
        st.json({
            "Name": extracted_data.get("name"),
            "Email": extracted_data.get("email"),
            "Phone": extracted_data.get("mobile"),
            "DOB": extracted_data.get("dob"),
            "Gender": extracted_data.get("gender"),
        })

    except Exception as e:
        st.error(f"Processing failed: {e}")

    finally:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
