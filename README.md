Streamlit Resume NER Pipeline

This project is an end-to-end data pipeline that extracts structured information from resumes using a custom NER model and makes it available for analytics and reporting.

Users upload resumes through a Streamlit app. The system extracts key personal details, stores structured data in a database, stages files in S3, and pushes data to Snowflake for reporting in Power BI.

What this project does

Upload resumes through a Streamlit web interface

Apply a custom NER model to extract:

Name

Email

Phone number

Gender

Date of birth

Store extracted fields in MySQL

Store original resumes in AWS S3

Load data from S3 into Snowflake

Enable analytics and dashboards in Power BI


High-level architecture

Streamlit App
→ NER Model (Python)
→ MySQL (structured candidate data)
→ AWS S3 (resume storage and staging)
→ Snowflake (analytics warehouse)
→ Power BI (visualization)

Tech stack

Python

Streamlit

Custom NER model

MySQL

AWS S3

Snowflake

Power BI


Repository structure
.
├── streamlit_app.py            # Streamlit UI for resume upload
├── resume_ner_to_mysql.py      # NER extraction and DB logic
├── requirements.txt            # Python dependencies
├── .env.example                # Environment variable template
├── .gitignore
└── README.md


Environment variables

All credentials are managed using environment variables and are not committed to GitHub.

Create a local .env file using .env.example as a reference.
DB_HOST=
DB_USER=
DB_PASSWORD=
DB_NAME=

AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_S3_BUCKET=

Running the project locally

Create and activate a virtual environment

Install dependencies

Set up the .env file

Run the Streamlit app

pip install -r requirements.txt
streamlit run streamlit_app.py


Use case

This project demonstrates:

Applied NLP with NER

Data engineering pipelines

Cloud storage and warehousing

Analytics-ready data modeling
