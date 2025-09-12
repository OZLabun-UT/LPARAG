from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv
import os
import boto3
import re

# Load secrets from .env
load_dotenv(override=True)
print("DEBUG ENV AWS_REGION:", os.getenv("AWS_REGION"))
print("DEBUG ENV KB_ID:", os.getenv("KB_ID"))
print("DEBUG ENV MODEL_ID:", os.getenv("MODEL_ID"))

KB_ID = os.getenv("KB_ID")
REGION = ("us-east-2")
MODEL_ID = os.getenv("MODEL_ID", "anthropic.claude-sonnet-4-20250514-v1:0")

# Build full ARN for model
MODEL_ARN = os.getenv("MODEL_ARN")


# Create Bedrock Agent Runtime client
bedrock_agent = boto3.client("bedrock-agent-runtime", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)
app = FastAPI()


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
        if (doc.url.match(/\.(png|jpg|jpeg|gif)$/i)) {
          html += `<li><img src="${doc.url}" style="max-width:400px;"></li>`;
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


def parse_s3_uri(uri: str):
    """Split s3://bucket/key into (bucket, key)."""
    match = re.match(r"s3://([^/]+)/(.+)", uri)
    if not match:
        return None, None
    return match.group(1), match.group(2)


@app.post("/query")
async def query_kb(query: dict):
    """Send user query to Bedrock KB and return text + PDFs/images."""
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

        # Model’s answer
        answer = response["output"]["text"]

        # Collect presigned URLs for any cited docs
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
                        ExpiresIn=3600,  # link valid for 1 hour
                    )
                    links.append({
                        "source": s3_uri,
                        "url": presigned_url
                    })

        return {
            "answer": answer,
            "documents": links
        }

    except Exception as e:
        return {"error": str(e)}