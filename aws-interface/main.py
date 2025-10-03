from fastapi import FastAPI, File, UploadFile
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv
import os
import boto3
import re
import mimetypes
from pathlib import Path
import shutil
import uvicorn

# Load secrets from .env
load_dotenv(override=True)

KB_ID = os.getenv("KB_ID")
REGION = "us-east-2"
MODEL_ARN = os.getenv("MODEL_ARN")

# AWS clients
bedrock_agent = boto3.client("bedrock-agent-runtime", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)

app = FastAPI()

# -----------------------
# Utility
# -----------------------
def parse_s3_uri(uri: str):
    """Split s3://bucket/key into (bucket, key)."""
    match = re.match(r"s3://([^/]+)/(.+)", uri)
    if not match:
        return None, None
    return match.group(1), match.group(2)


def make_presigned(bucket: str, key: str):
    """Generate presigned URL + MIME type for an S3 object."""
    presigned_url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=3600,
    )
    mime_type, _ = mimetypes.guess_type(key)
    if not mime_type:
        mime_type = "application/octet-stream"
    return {
        "source": f"s3://{bucket}/{key}",
        "url": presigned_url,
        "mime_type": mime_type,
    }


# -----------------------
# UI
# -----------------------
@app.get("/", response_class=HTMLResponse)
def serve_ui():
    return """
    <!DOCTYPE html>
    <html>
    <head>
      <title>Knowledge Base Chat</title>
      <style>
        body { font-family: Arial, sans-serif; margin: 2rem; }
        #answer { margin-top: 1rem; padding: 1rem; border: 1px solid #ccc; }
        img, svg { margin: 5px; border: 1px solid #ccc; max-width: 400px; }
      </style>
    </head>
    <body>
      <h1>Ask the Knowledge Base</h1>
      <input type="text" id="question" placeholder="Type your question..." size="50"/>
      <button onclick="ask()">Ask</button>
      <div id="answer"></div>

      <h2>Upload a PDF</h2>
      <form id="uploadForm" enctype="multipart/form-data">
        <input type="file" id="fileInput" accept="application/pdf"/>
        <button type="submit">Upload</button>
      </form>
      <div id="uploadResult"></div>

      <script>
        async function ask() {
          const q = document.getElementById("question").value;
          const res = await fetch("/query", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ question: q })
          });
          const data = await res.json();

          let html = "";

          if (data.answer) {
            html += "<p><b>Answer:</b> " + data.answer + "</p>";
          }
          if (data.documents && data.documents.length > 0) {
            html += "<h3>References:</h3><ul>";
            for (const doc of data.documents) {
              if (doc.mime_type && doc.mime_type.startsWith("image/")) {
                html += `<li><img src="${doc.url}" alt="Figure"></li>`;
              } else if (doc.mime_type === "image/svg+xml") {
                html += `<li><object type="image/svg+xml" data="${doc.url}" width="400"></object></li>`;
              } else if (doc.mime_type === "application/pdf") {
                html += `<li><a href="${doc.url}" target="_blank">📄 PDF: ${doc.source}</a></li>`;
              } else {
                html += `<li><a href="${doc.url}" target="_blank">${doc.source}</a></li>`;
              }
            }
            html += "</ul>";
          }
          if (data.error) {
            html += "<p style='color:red'>Error: " + data.error + "</p>";
          }

          document.getElementById("answer").innerHTML = html;
        }

        document.getElementById("uploadForm").onsubmit = async (e) => {
          e.preventDefault();
          const file = document.getElementById("fileInput").files[0];
          const formData = new FormData();
          formData.append("file", file);

          const res = await fetch("/upload", {
            method: "POST",
            body: formData
          });
          const data = await res.json();
          document.getElementById("uploadResult").innerText =
            data.status === "ok"
              ? "✅ Uploaded to " + data.path
              : "❌ Error: " + (data.error || "unknown");
        };
      </script>
    </body>
    </html>
    """


# -----------------------
# Query KB
# -----------------------
@app.post("/query")
async def query_kb(query: dict):
    try:
        response = bedrock_agent.retrieve_and_generate(
            input={"text": query["question"]},
            retrieveAndGenerateConfiguration={
                "knowledgeBaseConfiguration": {
                    "knowledgeBaseId": KB_ID,
                    "modelArn": MODEL_ARN,
                },
                "type": "KNOWLEDGE_BASE",
            },
        )

        answer = response["output"]["text"]
        citations = response.get("citations", [])
        links = []

        for citation in citations:
            for ref in citation.get("retrievedReferences", []):
                s3_uri = ref["location"]["s3Location"]["uri"]
                bucket, key = parse_s3_uri(s3_uri)
                if not (bucket and key):
                    continue

                # Always add the original reference
                links.append(make_presigned(bucket, key))

                # If it's a text page, try to collect images/vectors/figures
                if "/text/" in key:
                    base_prefix, page_file = key.split("/text/")
                    page_id = os.path.splitext(os.path.basename(page_file))[0]  # e.g. "page_3"

                    for folder in ["images", "vectors", "figures"]:
                        prefix = f"{base_prefix}/{folder}/{page_id}"
                        resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)

                        if "Contents" not in resp:
                            continue  # no such folder or no files

                        count = 0
                        for obj in resp["Contents"]:
                            if count >= 3:
                                break
                            links.append(make_presigned(bucket, obj["Key"]))
                            count += 1

        return {"answer": answer, "documents": links}

    except Exception as e:
        return {"error": str(e)}


# -----------------------
# Upload PDFs locally
# -----------------------
BASE_DIR = Path(__file__).resolve().parent.parent  # goes up from aws-interface
UPLOAD_DIR = BASE_DIR / "pdf-chunker" / "pdfs"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    try:
        file_path = UPLOAD_DIR / file.filename
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        return {"status": "ok", "path": str(file_path)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
