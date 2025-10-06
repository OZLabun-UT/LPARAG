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

# Load secrets
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

    # Generate friendly name
    name = os.path.splitext(os.path.basename(key))[0]
    display_name = (
        name.replace("_", " ").replace("-", " ").title().strip()
    )  # "Shrock24 Gev" from "Shrock24_GeV"
    return {
        "source": f"s3://{bucket}/{key}",
        "url": presigned_url,
        "mime_type": mime_type,
        "display_name": display_name,
    }


# -----------------------
# UI
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
        h1 {
          font-size: 1.8rem;
          font-weight: 600;
          margin-bottom: 1rem;
          color: #111;
        }
        input[type="text"], input[type="number"] {
          padding: 0.6rem 1rem;
          border-radius: 6px;
          border: 1px solid #ccc;
          font-size: 1rem;
        }
        input[type="number"] {
          width: 80px;
          margin-left: 0.6rem;
        }
        label {
          margin-left: 0.6rem;
          font-weight: 500;
        }
        button {
          padding: 0.6rem 1.2rem;
          margin-left: 0.5rem;
          background-color: #2563eb;
          color: white;
          border: none;
          border-radius: 6px;
          cursor: pointer;
          font-weight: 500;
          transition: background 0.2s;
        }
        button:hover { background-color: #1e4fc9; }

        #tabs {
          display: flex;
          flex-wrap: wrap;
          gap: 0.5rem;
          margin-top: 1.5rem;
        }
        .tab {
          background: #e5e7eb;
          padding: 0.4rem 0.8rem;
          border-radius: 6px;
          cursor: pointer;
          transition: background 0.2s;
          user-select: none;
        }
        .tab.active {
          background: #2563eb;
          color: white;
          font-weight: 600;
        }

        .tab-content {
          display: none;
          margin-top: 1rem;
          padding: 1.5rem;
          background: white;
          border-radius: 12px;
          box-shadow: 0 2px 6px rgba(0,0,0,0.08);
        }
        .tab-content.active { display: block; }

        .pdf-button {
          display: inline-block;
          background-color: #059669;
          color: white;
          padding: 0.5rem 1rem;
          border-radius: 6px;
          text-decoration: none;
          font-weight: 600;
          transition: background 0.2s;
          margin-bottom: 1rem;
          margin-top: 0.5rem;
        }
        .pdf-button:hover { background-color: #047857; }

        h3 {
          margin-top: 1.2rem;
          border-bottom: 2px solid #e5e7eb;
          padding-bottom: 0.4rem;
          color: #111;
        }

        .section { margin-top: 1.2rem; }

        /* --- Layout improvements --- */
        .flex-row {
          display: flex;
          flex-wrap: wrap;
          gap: 1rem;
          align-items: flex-start;
        }
        .text-col {
          flex: 2;
          min-width: 260px;
        }
        .img-col {
          flex: 1.2;
          min-width: 200px;
          display: flex;
          flex-direction: column;
          gap: 0.6rem;
        }
        img, svg {
          border: 1px solid #ddd;
          border-radius: 6px;
          max-width: 100%;
          height: auto;
        }

        ul { padding-left: 1.2rem; }
        li { margin-bottom: 0.6rem; }

        .upload-section {
          margin-top: 2rem;
          background: white;
          padding: 1rem 1.5rem;
          border-radius: 12px;
          box-shadow: 0 2px 6px rgba(0,0,0,0.08);
        }
      </style>
    </head>
    <body>
      <h1>Knowledge Base Chat</h1>
      <div>
        <input type="text" id="question" placeholder="Type your question..." size="50" />
        <label for="imgLimit"># Images</label>
        <input type="number" id="imgLimit" min="1" max="20" value="8" />
        <button onclick="ask()">Ask</button>
      </div>

      <div id="tabs"></div>
      <div id="tab-contents"></div>

      <div class="upload-section">
        <h2>Upload a PDF</h2>
        <form id="uploadForm" enctype="multipart/form-data">
          <input type="file" id="fileInput" accept="application/pdf"/>
          <button type="submit">Upload</button>
        </form>
        <div id="uploadResult"></div>
      </div>

      <script>
        let queryCount = 0;

        async function ask() {
          const q = document.getElementById("question").value.trim();
          const imgLimit = parseInt(document.getElementById("imgLimit").value) || 8;
          if (!q) return alert("Please enter a question.");

          queryCount += 1;
          const tabId = "tab" + queryCount;

          const res = await fetch("/query", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ question: q, image_limit: imgLimit })
          });
          const data = await res.json();

          let html = `<div class="section"><p><strong>Question:</strong> ${q}</p></div>`;

          if (data.answer) {
            html += `<div class="section"><strong>Answer:</strong> ${data.answer}</div>`;
          }

          if (data.pdfs && data.pdfs.length > 0) {
            html += `<div class="section"><h3>PDFs</h3>`;
            for (const pdf of data.pdfs) {
              const name = pdf.display_name || "View PDF";
              html += `<a href="${pdf.url}" target="_blank" class="pdf-button">📄 ${name}</a>`;
            }
            html += `</div>`;
          }

          const imgs = (data.documents || []).filter(d => d.mime_type && d.mime_type.startsWith("image/"));
          if (imgs.length > 0) {
            html += `<div class="section"><h3>Figures (${imgs.length})</h3><div class="flex-row img-col">`;
            for (const img of imgs) {
              html += `<img src="${img.url}" alt="Figure">`;
            }
            html += "</div></div>";
          }

          const refs = (data.documents || []).filter(d => !d.mime_type?.startsWith("image/"));
          if (refs.length > 0) {
            html += `<div class="section"><h3>References</h3><ul>`;
            for (const doc of refs) {
              if (doc.source.endsWith(".json") || doc.source.endsWith(".txt")) continue;
              html += `<li><a href="${doc.url}" target="_blank">${doc.source}</a></li>`;
            }
            html += "</ul></div>";
          }

          if (data.error) html += `<p style='color:red'>Error: ${data.error}</p>`;

          const tab = document.createElement("div");
          tab.className = "tab";
          tab.id = tabId + "-tab";
          tab.innerText = "Query " + queryCount;
          tab.onclick = () => switchTab(tabId);

          const content = document.createElement("div");
          content.className = "tab-content";
          content.id = tabId;
          content.innerHTML = html;

          document.getElementById("tabs").appendChild(tab);
          document.getElementById("tab-contents").appendChild(content);

          switchTab(tabId);
        }

        function switchTab(tabId) {
          document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
          document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));
          document.getElementById(tabId + "-tab").classList.add("active");
          document.getElementById(tabId).classList.add("active");
        }

        document.getElementById("uploadForm").onsubmit = async (e) => {
          e.preventDefault();
          const file = document.getElementById("fileInput").files[0];
          if (!file) return alert("Please choose a file first.");
          const formData = new FormData();
          formData.append("file", file);

          const res = await fetch("/upload", { method: "POST", body: formData });
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
        image_limit = int(query.get("image_limit", 8))

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
        pdf_links = []
        processed_folders = {}
        image_count = 0  # ✅ track total images added globally

        for citation in citations:
            for ref in citation.get("retrievedReferences", []):
                if image_count >= image_limit:
                    break  # ✅ stop once limit reached globally

                s3_uri = ref["location"]["s3Location"]["uri"]
                bucket, key = parse_s3_uri(s3_uri)
                if not (bucket and key):
                    continue

                # Skip JSON/TXT direct links but still scan folders
                if not (key.lower().endswith(".json") or key.lower().endswith(".txt")):
                    links.append(make_presigned(bucket, key))

                # Determine base folder
                parts = key.split("/")
                base_dir = "/".join(parts[:2]) if len(parts) >= 2 else parts[0]
                if base_dir in processed_folders:
                    continue
                processed_folders[base_dir] = set()

                image_keys = set()

                def try_list(prefix):
                    try:
                        resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
                        return [obj["Key"] for obj in resp.get("Contents", [])]
                    except Exception:
                        return []

                # 1️⃣ images/
                for k in try_list(f"{base_dir}/images/"):
                    if k.lower().endswith((".png", ".jpg", ".jpeg", ".svg")):
                        image_keys.add(k)

                # 2️⃣ output/
                for k in try_list(f"{base_dir}/output/"):
                    if k.lower().endswith((".png", ".jpg", ".jpeg", ".svg")):
                        image_keys.add(k)

                # ✅ Add up to remaining available slots
                available_slots = image_limit - image_count
                for k in list(image_keys)[:available_slots]:
                    links.append(make_presigned(bucket, k))
                    image_count += 1
                    if image_count >= image_limit:
                        break

                # 3️⃣ Add first PDF if found
                for k in try_list(f"{base_dir}/"):
                    if k.lower().endswith(".pdf"):
                        pdf_links.append(make_presigned(bucket, k))
                        break

            if image_count >= image_limit:
                break  # ✅ double-break to stop outer loop too

        return {"answer": answer, "documents": links, "pdfs": pdf_links}

    except Exception as e:
        return {"error": str(e)}

# -----------------------
# Upload PDFs locally
# -----------------------
BASE_DIR = Path(__file__).resolve().parent.parent
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
