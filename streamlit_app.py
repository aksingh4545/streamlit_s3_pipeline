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
s3 = boto3.client("s3")

# --------------------------------------------------
# STREAMLIT CONFIG
# --------------------------------------------------
st.set_page_config(
    page_title="Resume NER Processing",
    layout="centered"
)

st.title("Resume NER Processing")
st.write(
    "Upload one or multiple resumes in any format. "
    "The system extracts entities, stores them in MySQL, "
    "and uploads CSV output to S3."
)

# --------------------------------------------------
# FILE UPLOADER (MULTIPLE)
# --------------------------------------------------
uploaded_files = st.file_uploader(
    "Upload Resume Files",
    accept_multiple_files=True
)

# --------------------------------------------------
# PROCESS BUTTON
# --------------------------------------------------
if uploaded_files:
    st.success(f"{len(uploaded_files)} file(s) selected")

    if st.button("Process Resumes"):
        temp_paths = []
        file_map = {}

        with st.spinner("Processing resumes..."):
            try:
                # Save uploaded files temporarily
                for file in uploaded_files:
                    suffix = os.path.splitext(file.name)[1]

                    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                        tmp.write(file.read())
                        temp_paths.append(tmp.name)
                        file_map[tmp.name] = file.name

                    # Optional: upload original file to S3
                    s3.upload_file(
                        tmp.name,
                        BUCKET_NAME,
                        f"uploaded/{file.name}"
                    )

                # Run pipeline (supports list)
                results = run_pipeline(temp_paths)

                st.success("Processing completed")

                # --------------------------------------------------
                # RESULTS DISPLAY
                # --------------------------------------------------
                for res in results:
                    st.divider()

                    display_name = file_map.get(res["file"], res["file"])
                    st.subheader(display_name)

                    if res["status"] == "success":
                        st.json(res["data"])
                    else:
                        if "No readable text" in res["error"]:
                            st.warning(
                                "Scanned PDF detected. "
                                "Please upload a text-based PDF or DOCX."
                            )
                        else:
                            st.error(res["error"])

            except Exception as e:
                st.error(str(e))

            finally:
                # Cleanup temp files
                for path in temp_paths:
                    if os.path.exists(path):
                        os.remove(path)
