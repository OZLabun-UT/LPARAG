from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv
import os
import boto3
import re
import mimetypes

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


def make_presigned_doc(s3_uri: str):
    """Generate presigned URL + MIME type for an S3 object."""
    bucket, key = parse_s3_uri(s3_uri)
    if not bucket or not key:
        return None

    presigned_url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=3600,
    )
    mime_type, _ = mimetypes.guess_type(key)
    if not mime_type:
        mime_type = "application/octet-stream"

    return {"source": s3_uri, "url": presigned_url, "mime_type": mime_type}


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
        img { margin: 5px; border: 1px solid #ccc; max-width: 400px; }
      </style>
    </head>
    <body>
      <h1>Ask the Knowledge Base</h1>
      <input type="text" id="question" placeholder="Type your question..." size="50"/>
      <button onclick="ask()">Ask</button>
      <div id="answer"></div>

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
      </script>
    </body>
    </html>
    """


def make_presigned_doc(s3_uri: str):
    bucket, key = parse_s3_uri(s3_uri)
    if not bucket or not key:
        return None
    presigned_url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=3600,
    )
    mime_type, _ = mimetypes.guess_type(key)
    return {"source": s3_uri, "url": presigned_url, "mime_type": mime_type or "application/octet-stream"}


import re

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

                if bucket and key:
                    presigned_url = s3.generate_presigned_url(
                        "get_object",
                        Params={"Bucket": bucket, "Key": key},
                        ExpiresIn=3600,
                    )
                    links.append({
                        "source": s3_uri,
                        "url": presigned_url,
                        "mime_type": mimetypes.guess_type(key)[0] or "application/octet-stream"
                    })

                # If this is a text page, find the sibling image
                if "/text/page" in key:
                    base_prefix, page_file = key.split("/text/")
                    page_id = os.path.splitext(os.path.basename(page_file))[0]  # e.g. "page_7"

                    # normalize: page_7 → page7
                    page_id_norm = re.sub(r"_(\d+)", r"\1", page_id)

                    img_prefix = f"{base_prefix}/images/{page_id_norm}_img"
                    resp = s3.list_objects_v2(Bucket=bucket, Prefix=img_prefix)

                    if "Contents" in resp:
                        for obj in resp["Contents"]:
                            if obj["Key"].lower().endswith((".png", ".jpg", ".jpeg")):
                                presigned_url = s3.generate_presigned_url(
                                    "get_object",
                                    Params={"Bucket": bucket, "Key": obj["Key"]},
                                    ExpiresIn=3600,
                                )
                                links.append({
                                    "source": f"s3://{bucket}/{obj['Key']}",
                                    "url": presigned_url,
                                    "mime_type": "image/jpeg"
                                })
                                break  # only attach the first image match

        return {"answer": answer, "documents": links}

    except Exception as e:
        return {"error": str(e)}

