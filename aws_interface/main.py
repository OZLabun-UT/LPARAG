from fastapi import FastAPI, File, UploadFile, Request, Form
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv
import os
import boto3
import re
import mimetypes
from pathlib import Path
import shutil
import uvicorn
from uuid import uuid4
import tempfile

# ✅ Import Docling chunker text extractor
from pdf_chunker.new_chunker import extract_text_for_session

# -----------------------
# Setup
# -----------------------
load_dotenv(override=True)
KB_ID = os.getenv("KB_ID")
REGION = "us-east-2"
MODEL_ARN = os.getenv("MODEL_ARN")

bedrock_agent = boto3.client("bedrock-agent-runtime", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)
app = FastAPI()

# In-memory conversation + temporary data
session_state = {}  # { session_id: { "history": [], "citations": [], "temp_pdfs": [ {"filename": str, "text": str} ] } }

# -----------------------
# Utility Functions
# -----------------------
def parse_s3_uri(uri: str):
    match = re.match(r"s3://([^/]+)/(.+)", uri)
    if not match:
        return None, None
    return match.group(1), match.group(2)

def make_presigned(bucket: str, key: str):
    presigned_url = s3.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=3600
    )
    mime_type, _ = mimetypes.guess_type(key)
    if not mime_type:
        mime_type = "application/octet-stream"
    name = os.path.splitext(os.path.basename(key))[0]
    display_name = name.replace("_", " ").replace("-", " ").title().strip()
    return {
        "source": f"s3://{bucket}/{key}",
        "url": presigned_url,
        "mime_type": mime_type,
        "display_name": display_name,
    }

# -----------------------
# Frontend UI
# -----------------------
@app.get("/", response_class=HTMLResponse)
def serve_ui():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8" />
      <title>Knowledge Base Chat</title>
      <style>
        body {
          font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
          background-color: #f5f7fa;
          margin: 0;
          padding: 2rem;
          color: #222;
          line-height: 1.5;
        }
        h1 { font-size: 1.8rem; font-weight: 600; margin-bottom: 1rem; color: #111; }
        #chat {
          background: #fff; padding: 1rem; border-radius: 10px;
          box-shadow: 0 2px 6px rgba(0,0,0,0.1); max-height: 70vh; overflow-y: auto; margin-bottom: 1rem;
        }
        .user-msg, .assistant-msg { margin: 0.6rem 0; padding: 0.6rem 0.9rem; border-radius: 8px; line-height: 1.4; }
        .user-msg { background: #e5e7eb; text-align: right; }
        .assistant-msg { background: #2563eb20; text-align: left; }
        input[type="text"], input[type="number"] {
          padding: 0.6rem 1rem; border-radius: 6px; border: 1px solid #ccc; font-size: 1rem;
        }
        input[type="number"] { width: 80px; margin-left: 0.6rem; }
        label { margin-left: 0.6rem; font-weight: 500; }
        button {
          padding: 0.6rem 1.2rem; margin-left: 0.5rem; background-color: #2563eb; color: white;
          border: none; border-radius: 6px; cursor: pointer; font-weight: 500; transition: background 0.2s;
        }
        button:hover { background-color: #1e4fc9; }
        .pdf-button {
          display: inline-block; background-color: #059669; color: white; padding: 0.4rem 0.8rem;
          border-radius: 6px; text-decoration: none; font-weight: 600; margin: 0.3rem;
        }
        .pdf-button:hover { background-color: #047857; }
        .upload-section {
          margin-top: 2rem; background: white; padding: 1rem 1.5rem;
          border-radius: 12px; box-shadow: 0 2px 6px rgba(0,0,0,0.08);
        }
        #loading, #uploading {
          display: none; align-items: center; gap: 10px; margin-bottom: 10px; font-weight: 500; color: #2563eb;
        }
        .loader {
          width: 22px; height: 22px; border: 3px solid #93c5fd;
          border-top: 3px solid #2563eb; border-radius: 50%;
          animation: spin 0.8s linear infinite;
        }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
      </style>
    </head>
    <body>
      <h1>Knowledge Base Chat</h1>

      <div id="loading"><div class="loader"></div><span>Thinking...</span></div>
      <div id="uploading"><div class="loader"></div><span>Uploading PDF...</span></div>

      <div id="chat"></div>

      <div style="margin-top:1rem;">
        <input type="text" id="question" placeholder="Type a message..." size="50" />
        <label for="imgLimit"># Images</label>
        <input type="number" id="imgLimit" min="1" max="20" value="8" />
        <button onclick="sendMessage()">Send</button>
      </div>

      <div class="upload-section">
        <h2>Permanent PDF Upload</h2>
        <form id="permUploadForm" enctype="multipart/form-data">
          <input type="file" id="permFileInput" accept="application/pdf"/>
          <button type="submit">Upload Permanently</button>
        </form>
        <div id="permUploadResult"></div>
      </div>

      <div class="upload-section">
        <h2>Temporary PDF Upload (for current chat only)</h2>
        <form id="tempUploadForm" enctype="multipart/form-data">
          <input type="file" id="tempFileInput" accept="application/pdf"/>
          <button type="submit">Upload Temporarily</button>
        </form>
        <div id="tempUploadResult"></div>
      </div>

      <script>
        let sessionId = null;

        async function sendMessage() {
          const q = document.getElementById("question").value.trim();
          const imgLimit = parseInt(document.getElementById("imgLimit").value) || 8;
          if (!q) return;

          const chatBox = document.getElementById("chat");
          const loadingBar = document.getElementById("loading");
          chatBox.innerHTML += `<div class='user-msg'><strong>You:</strong> ${q}</div>`;
          document.getElementById("question").value = "";
          chatBox.scrollTop = chatBox.scrollHeight;
          loadingBar.style.display = "flex";

          try {
            const res = await fetch("/query", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ question: q, image_limit: imgLimit, session_id: sessionId })
            });

            const data = await res.json();
            if (!sessionId && data.session_id) sessionId = data.session_id;

            let msgHtml = "";
            if (data.error) {
              msgHtml = `<div class='assistant-msg' style='color:red;'>Error: ${data.error}</div>`;
            } else {
              msgHtml = `<div class='assistant-msg'><strong>Assistant:</strong> ${data.answer || "(no response)"}`;
              if (data.pdfs && data.pdfs.length > 0) {
                msgHtml += "<div><h4>PDFs:</h4>";
                for (const pdf of data.pdfs)
                  msgHtml += `<a href='${pdf.url}' target='_blank' class='pdf-button'>📄 ${pdf.display_name}</a>`;
                msgHtml += "</div>";
              }
              const imgs = (data.documents || []).filter(d => d.mime_type && d.mime_type.startsWith("image/"));
              if (imgs.length > 0) {
                msgHtml += "<div><h4>Figures:</h4>";
                for (const img of imgs)
                  msgHtml += `<img src='${img.url}' alt='Figure' style='max-width:200px; margin:0.3rem; border-radius:6px;'>`;
                msgHtml += "</div>";
              }
              msgHtml += "</div>";
            }
            chatBox.innerHTML += msgHtml;
            chatBox.scrollTop = chatBox.scrollHeight;
          } catch (err) {
            chatBox.innerHTML += `<div class='assistant-msg' style='color:red;'>Network error: ${err}</div>`;
          } finally {
            loadingBar.style.display = "none";
          }
        }

        document.getElementById("permUploadForm").onsubmit = async (e) => {
          e.preventDefault();
          const file = document.getElementById("permFileInput").files[0];
          if (!file) return alert("Please choose a file.");
          const formData = new FormData();
          formData.append("file", file);
          const res = await fetch("/upload_permanent", { method: "POST", body: formData });
          const data = await res.json();
          document.getElementById("permUploadResult").innerText =
            data.status === "ok" ? "✅ Uploaded to " + data.path : "❌ Error: " + (data.error || "unknown");
        };

        document.getElementById("tempUploadForm").onsubmit = async (e) => {
          e.preventDefault();
          const file = document.getElementById("tempFileInput").files[0];
          if (!file) return alert("Please choose a file.");
          if (!sessionId) sessionId = crypto.randomUUID();
          const uploadingBar = document.getElementById("uploading");
          uploadingBar.style.display = "flex";
          const formData = new FormData();
          formData.append("file", file);
          formData.append("session_id", sessionId);
          const res = await fetch("/upload_temporary", { method: "POST", body: formData });
          const data = await res.json();
          document.getElementById("tempUploadResult").innerText =
            data.status === "ok"
              ? `✅ Extracted ${data.chunk_count} chunks from ${data.filename}`
              : "❌ Error: " + (data.error || "unknown");
          uploadingBar.style.display = "none";
        };
      </script>
    </body>
    </html>
    """

# -----------------------
# Query Endpoint
# -----------------------
@app.post("/query")
async def query_kb(query: dict, request: Request):
    try:
        session_id = query.get("session_id") or str(uuid4())
        state = session_state.setdefault(session_id, {"history": [], "citations": [], "temp_pdfs": []})
        question = query["question"]
        image_limit = int(query.get("image_limit", 8))

        chat_context = "\n".join(
            [f"User: {h['user']}\nAssistant: {h['assistant']}" for h in state["history"][-3:]]
        )
        pdf_text = "\n\n".join([pdf["text"][:5000] for pdf in state["temp_pdfs"]])
        combined_prompt = f"{chat_context}\n\nRelevant PDF content:\n{pdf_text}\nUser: {question}"

        response = bedrock_agent.retrieve_and_generate(
            input={"text": combined_prompt},
            retrieveAndGenerateConfiguration={
                "knowledgeBaseConfiguration": {"knowledgeBaseId": KB_ID, "modelArn": MODEL_ARN},
                "type": "KNOWLEDGE_BASE",
            },
        )

        answer = response["output"]["text"]
        citations = response.get("citations", [])
        links, pdf_links, processed_folders, image_count = [], [], {}, 0

        for citation in citations:
            for ref in citation.get("retrievedReferences", []):
                if image_count >= image_limit:
                    break
                s3_uri = ref["location"]["s3Location"]["uri"]
                bucket, key = parse_s3_uri(s3_uri)
                if not (bucket and key):
                    continue
                if not (key.lower().endswith(".json") or key.lower().endswith(".txt")):
                    links.append(make_presigned(bucket, key))
                parts = key.split("/")
                base_dir = "/".join(parts[:2]) if len(parts) >= 2 else parts[0]
                if base_dir in processed_folders:
                    continue
                processed_folders[base_dir] = set()

                def try_list(prefix):
                    try:
                        resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
                        return [obj["Key"] for obj in resp.get("Contents", [])]
                    except Exception:
                        return []

                for folder in ["images/", "output/"]:
                    for k in try_list(f"{base_dir}/{folder}"):
                        if k.lower().endswith((".png", ".jpg", ".jpeg", ".svg")):
                            if image_count >= image_limit:
                                break
                            links.append(make_presigned(bucket, k))
                            image_count += 1
                for k in try_list(f"{base_dir}/"):
                    if k.lower().endswith(".pdf"):
                        pdf_links.append(make_presigned(bucket, k))
                        break
            if image_count >= image_limit:
                break

        state["history"].append({"user": question, "assistant": answer})
        state["citations"].extend(citations)
        return {"session_id": session_id, "answer": answer, "documents": links, "pdfs": pdf_links}

    except Exception as e:
        return {"error": str(e)}

# -----------------------
# Upload Endpoints
# -----------------------
BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "pdf_chunker" / "pdfs"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

@app.post("/upload_permanent")
async def upload_permanent(file: UploadFile = File(...)):
    try:
        file_path = UPLOAD_DIR / file.filename
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        return {"status": "ok", "path": str(file_path)}
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.post("/upload_temporary")
async def upload_temporary(request: Request, file: UploadFile = File(...), session_id: str = Form(...)):
    try:
        state = session_state.setdefault(session_id, {"history": [], "citations": [], "temp_pdfs": []})
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = Path(tmp.name)
        extracted_text = extract_text_for_session(tmp_path)
        state["temp_pdfs"].append({"filename": file.filename, "text": extracted_text})
        return {"status": "ok", "filename": file.filename, "chunk_count": len(extracted_text.split("\\n\\n"))}
    except Exception as e:
        return {"status": "error", "error": str(e)}

# -----------------------
# Run
# -----------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
