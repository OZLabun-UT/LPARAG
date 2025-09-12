from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv
import os
import boto3

# Load secrets from .env
load_dotenv()
KB_ID = os.getenv("KB_ID")
REGION = os.getenv("AWS_REGION", "us-east-1")
MODEL_ID = os.getenv("MODEL_ID", "anthropic.claude-v2")

# Build full ARN for model
MODEL_ARN = f"arn:aws:bedrock:{REGION}::model/{MODEL_ID}"

app = FastAPI()

# Create Bedrock Agent Runtime client (uses AWS creds from env automatically)
bedrock_agent = boto3.client("bedrock-agent-runtime", region_name=REGION)


@app.get("/", response_class=HTMLResponse)
def serve_ui():
    """Simple browser UI"""
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
          document.getElementById("answer").innerText = data.answer;
        }
      </script>
    </body>
    </html>
    """



@app.post("/query")
async def query_kb(query: dict):
    """Send user query to Bedrock KB"""
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

    return {"answer": response["output"]["text"]}
