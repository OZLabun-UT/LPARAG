# Ragatini - A Generalizable Topical Expert Agent

Ragatini is an AWS-enabled chatbot that retrieves and synthesizes answers to domain-specific questions using a user-built corpus. A standout feature of Ragatini is displaying relevant plots and citations from the referenced documents, which can aid in literature search and review. This repository contains the main chatbot funcitonality, web UI, PDF document processor, AWS S3 uploader and syncer, and an Arxiv scraper.

---

Ragatoni uses a lightweight classifier LLM to route each query to the most appropriate populated AWS knowledge base, which allows flexibility in user topic choice. This query is sent to the appropriate user-controlled knowledge bases, and a response is generated and returned.

Currently supported domains:

- **Laser Wakefield Acceleration: Simulation**
- **Laser Wakefield Acceleration: Experimental**
- **Plasma Wakefield Acceleration**
- **Structure Wakefield Acceleration**

---

### 📄 Scientific PDF Ingestion
Ragatoni includes a full ingestion pipeline for scientific PDFs using the unstructured library. Documents are parsed and multiple components are saved:

- **Text:** Chunks of text are extracted and saved to multiple txt files.
- **Images:** Figures and images are extracted and saved individually.
- **Structure JSON:** A JSON file that includes the structure of the paper as well as metadata.

---

### ☁️ AWS Bedrock Integration
Ragatoni relies on **AWS Bedrock Knowledge Bases**. Documents that are deconstructed are uploaded to AWS S3 buckets, where they can act as data sources for knowledge bases. Knowledge bases are RAG agents that have access to the documents and can answer domain-relevant questions and return topical documents to the user. Ragatini has figure retrieval functionality that shows extracted images and figures from relevant documents, and will show citations along with the relevance score for each document.

## System Architecture

User Query

   ↓

Domain Router (LLM classifier)

   ↓

Selected Bedrock Knowledge Base

   ↓

Vector Retrieval (chunks + metadata)

   ↓

Figure / PDF Reconstruction (S3)

   ↓

Context Assembly (chat + PDFs + figures)

   ↓

LLM Generation

   ↓

Answer + Figures + PDFs + Scores


ragtime/

├── main.py                    # FastAPI application

├── aws_interface/

│   └── master_router.py       # Domain routing + KB dispatch

├── pdf_chunker/

│   ├── new_chunker.py         # Docling-based extraction

│   ├── chunker.py             # PyMuPDF + OpenCV pipeline

│   ├── fine_grain_chunker.py  # Experimental chunking

│   ├── s3_push.py             # Upload + KB resync

│   └── s3_delete.py           # Cleanup utilities

├── static/

│   ├── chat.html              # Main UI

│   ├── batch_upload.html      # Batch PDF upload UI

│   └── arxiv_scraper.html     # arXiv ingestion UI

├── requirements.txt

└── systemd/

    └── ragatini.service        # Production deployment


# Setup

To set up Ragatini, an AWS account is needed, and full Bedrock and S3 permissions are necessary. Additionally, it is convenient to run instances of Ragatini on AWS EC2, so permissions for EC2 are also recommended. After creating an account with the needed permissions, the following environment variables must be placed in an .env file:

*FILL THIS OUT*

The required packages can be installed using:

``pip install -r requirements.txt``

Afterwards, the Arxiv scraper can be used to populate S3 buckets. After downloading relevant PDFs, the following commands can be used to process them:

``cd pdf_chunker``
``python new_chunker.py ./pdfs``

Then, the processed documents can be uploaded to an S3 bucket using:

``python s3_push.py pdfs/ --bucket BUCKET-NAME --chunk-s3-only``

Where you can find your available buckets with the command:

``aws bedrock-agent list-knowledge-bases \ --region us-east-2 \ --output table``

Afterwards, connect your S3 bucket to a knowledge base through the AWS dashboard and save the knowledge base ID as an environment variable: **Specify which variable**

Then, simply run the main python file using:

``python3 -m aws_interface.main``

Congratulations! Now Ragatoni should be able to return answers to questions based on your uploaded documents.
