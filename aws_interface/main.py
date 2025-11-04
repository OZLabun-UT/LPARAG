from fastapi import FastAPI, File, UploadFile, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

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
import json
import subprocess
from pdf_chunker.new_chunker import extract_text_for_session
from io import BytesIO
from PIL import Image
from datetime import datetime


# -----------------------
# Environment & paths
# -----------------------
load_dotenv(override=True)

BASE_DIR = Path(__file__).resolve().parent.parent  # /rag-llm
PDF_CHUNKER_DIR = BASE_DIR / "pdf_chunker"
UPLOAD_DIR = PDF_CHUNKER_DIR / "pdfs"
OUTPUT_DIR = PDF_CHUNKER_DIR / "output"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

CHAT_PASSWORD = os.getenv("CHAT_PASSWORD")
KB_ID = os.getenv("KB_ID")
REGION = os.getenv("AWS_REGION", "us-east-2")
MODEL_ARN = os.getenv("MODEL_ARN")

debug=False

bedrock_agent = boto3.client("bedrock-agent-runtime", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)
app = FastAPI()

# In-memory state
session_state = {}

#Favicon 
BASE_DIR = Path(__file__).parent

# Serve the favicon directly
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(BASE_DIR / "favicon.ico")

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
# Auth Routes
# -----------------------
@app.get("/", response_class=HTMLResponse)
def serve_login_page():
    return r"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="UTF-8">
      <title>Chatbot Login</title>
      <style>
        body {
          background: #0d1117;
          color: #f3f4f6;
          display: flex;
          justify-content: center;
          align-items: center;
          height: 100vh;
          font-family: Inter, sans-serif;
        }
        .login-box {
          background: #1e2531;
          padding: 2rem 3rem;
          border-radius: 12px;
          box-shadow: 0 0 25px rgba(0,0,0,0.4);
          text-align: center;
        }
        input {
          margin-top: 1rem;
          width: 100%;
          padding: .7rem;
          border-radius: 8px;
          border: none;
          font-size: 1rem;
        }
        button {
          margin-top: 1.2rem;
          padding: .7rem;
          width: 100%;
          border: none;
          border-radius: 8px;
          background: linear-gradient(135deg, #7c3aed, #2563eb);
          color: white;
          font-weight: bold;
          cursor: pointer;
        }
        button:hover { opacity: .9; }
        p { color: #f87171; }
      </style>
    </head>
    <body>
      <div class="login-box">
        <h2>🔐 Enter Password</h2>
        <input type="password" id="pw" placeholder="Password"/>
        <button onclick="login()">Login</button>
        <p id="msg"></p>
      </div>
      <script>
        async function login() {
          const pw = document.getElementById("pw").value.trim();
          const res = await fetch("/login", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({password: pw})
          });
          const data = await res.json();
          if (data.status === "ok") {
            sessionStorage.setItem("auth", "true");
            window.location.href = "/chat";
          } else {
            document.getElementById("msg").textContent = "Incorrect password.";
          }
        }
      </script>
    </body>
    </html>
    """

@app.post("/login")
async def login(request: Request):
    body = await request.json()
    password = body.get("password", "")
    if password == CHAT_PASSWORD:
        return {"status": "ok"}
    return {"status": "error"}

@app.get("/chat", response_class=HTMLResponse)
def serve_chat_ui():
    """Serve the main chatbot UI (protected by login)."""
    return r"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8" />
      <script>
        if(!sessionStorage.getItem("auth")) {
          window.location.href = "/";
        }
      </script>
    </head>
    <body>
    """ + open(Path(__file__).resolve().parent / "chat.html").read() + """
    </body>
    </html>
    """

# -----------------------
# S3 + Resync
# -----------------------
@app.post("/s3_push")
async def s3_push():
    try:
        s3_script = PDF_CHUNKER_DIR / "s3_push.py"
        subprocess.run(["python", str(s3_script), str(OUTPUT_DIR)], check=True)
        return {"status": "ok", "message": "S3 sync completed and local cleanup done."}
    except subprocess.CalledProcessError as e:
        return {"status": "error", "message": f"S3 push failed: {e}"}

@app.post("/resync_kb")
async def resync_kb():
    try:
        s3_script = PDF_CHUNKER_DIR / "s3_push.py"
        subprocess.run(["python", str(s3_script), "--resync-only"], check=True)
        return {"status": "ok", "message": "Knowledge base resync started."}
    except subprocess.CalledProcessError as e:
        return {"status": "error", "message": f"Resync failed: {e}"}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {e}"}

def is_large_enough(bucket, key, min_size=90):
    """
    Returns True if image width and height are both >= min_size.
    Downloads only a small portion of the file for dimension check.
    """
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read()
        img = Image.open(BytesIO(body))
        w, h = img.size
        return w >= min_size and h >= min_size
    except Exception as e:
        print(f"[!] Could not read dimensions for {key}: {e}")
        return False

@app.post("/query")
async def query_kb(query: dict, request: Request):
    """
    Main endpoint: builds context, queries Bedrock retrieve(),
    processes structured JSON + figures, and returns PDFs with relevance scores.
    """
    try:
        session_id = query.get("session_id") or str(uuid4())
        state = session_state.setdefault(session_id, {"history": [], "citations": [], "temp_pdfs": []})
        question = query["question"]
        image_limit = int(query.get("image_limit", 8))

        # 1️⃣ Build prompt context
        combined_prompt = build_context(state, question, query)

        # 2️⃣ Run Bedrock retrieval
        response = run_retrieval(question)
        retrievals = response.get("retrievalResults", [])
        generated_answer = run_generation(question, retrievals)


        # 3️⃣ Process each retrieved paper
        all_links, pdf_links, total_images = [], [], 0
        processed_folders = set()

        for r_idx, result in enumerate(retrievals):
            s3_uri = result.get("location", {}).get("s3Location", {}).get("uri")
            score = result.get("score", 0.0)
            if not s3_uri:
                continue

            bucket, key = parse_s3_uri(s3_uri)
            if not (bucket and key):
                continue

            base_dir = "/".join(key.split("/")[:-1])
            if base_dir in processed_folders:
                continue
            processed_folders.add(base_dir)

            # Process one full paper (structured.json + figures + pdf)
            links, pdfs, img_count = process_paper(bucket, base_dir, score, image_limit - total_images)
            all_links.extend(links)
            pdf_links.extend(pdfs)
            total_images += img_count

            if total_images >= image_limit:
                break

        # 4️⃣ Compose response
        answer = generated_answer 
        state["history"].append({"user": question, "assistant": answer})
        state["citations"].extend(all_links)

        return {
            "session_id": session_id,
            "answer": answer,
            "documents": all_links,
            "pdfs": pdf_links,
            "citations": all_links,
        }

    except Exception as e:
        print(f"[❌] Query failed: {e}")
        import traceback; traceback.print_exc()
        return {"error": str(e)}

# ============================================================
# --------------------- HELPER FUNCTIONS ----------------------
# ============================================================

def build_context(state, question, query):
    """Combine chat, PDFs, and selected images into a unified text context."""
    chat_context = "\n".join(
        [f"User: {h['user']}\nAssistant: {h['assistant']}" for h in state["history"][-3:]]
    )
    pdf_text = "\n\n".join([pdf["text"][:5000] for pdf in state["temp_pdfs"]])

    image_context = ""
    for i, img in enumerate(query.get("selected_images", []), 1):
        image_context += (
            f"\n\n[Image {i}]\nCaption: {img.get('caption')}\nContext: {img.get('context')}\n"
            f"Source: {img.get('source')}\nNOTE: User selected this image."
        )

    return (
        f"{chat_context}\n\nRelevant PDF content:\n{pdf_text}"
        f"\n\nSelected Figures for Reference:{image_context}\n\nUser: {question}"
    )

def run_retrieval(question):
    """Call Bedrock retrieve() API and dump full JSON to disk."""
    response = bedrock_agent.retrieve(
        knowledgeBaseId=KB_ID,
        retrievalQuery={"text": question},
    )
    if debug:
        debug_path = Path(f"bedrock_debug_{datetime.now():%Y%m%d_%H%M%S}.json")
        with open(debug_path, "w", encoding="utf-8") as f:
            json.dump(response, f, indent=2, ensure_ascii=False)
        print(f"[🧠 Saved full Bedrock response to: {debug_path.resolve()}]")
    return response


def process_paper(bucket, base_dir, score, remaining_slots):
    """Handles one paper’s structured.json, figures, and PDF extraction."""
    links, pdf_links, total_added = [], [], 0

    structured_data = load_structured_json(bucket, base_dir)
    text_map = {t["self_ref"]: t for t in structured_data.get("texts", [])} if structured_data else {}
    pic_map = extract_caption_and_context(structured_data, text_map)

    valid_images = find_images_for_paper(bucket, base_dir)
    if not valid_images:
        print(f"[📂] No valid images under {base_dir}")

    for idx, key in enumerate(valid_images[:remaining_slots]):
        img_info = make_presigned(bucket, key)
        img_info.update(pic_map.get(idx, {"caption": "No caption", "context": "No context"}))
        img_info["relevance"] = round(float(score), 3)
        links.append(img_info)
        total_added += 1
        print(f"[🖼] Added image: {Path(key).name}")

    # Add one PDF per paper
    pdf_info = find_pdf_for_paper(bucket, base_dir, score)
    if pdf_info:
        pdf_links.append(pdf_info)

    return links, pdf_links, total_added


def load_structured_json(bucket, base_dir):
    """Loads structured.json for the given paper, if present."""
    key = f"{base_dir}/structured.json"
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        data = json.loads(obj["Body"].read())
        print(f"[📄] Loaded structured.json for {base_dir}")
        return data
    except Exception as e:
        print(f"[!] No structured.json found for {base_dir}: {e}")
        return {}


def find_images_for_paper(bucket, base_dir):
    """Collects all valid image keys across several common paths."""
    paper_name = Path(base_dir).name
    prefixes = [
        f"{base_dir}/output/{paper_name}/images/",
        f"{base_dir}/output/images/",
        f"{base_dir}/{paper_name}/images/",
        f"{base_dir}/images/",
    ]
    found = []
    for prefix in prefixes:
        try:
            resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
            for obj in resp.get("Contents", []):
                k = obj["Key"]
                if k.lower().endswith((".png", ".jpg", ".jpeg", ".svg")) and is_large_enough(bucket, k, 90):
                    found.append(k)
        except Exception:
            continue
    return sorted(set(found))

# CURRENTLY UNUSED
def extract_images_from_retrievals(retrievals, image_limit):
    """Extract image entries directly from Bedrock retrievalResults."""
    img_links = []
    seen_sources = set()

    for result in retrievals:
        score = result.get("score", 0.0)
        content = result.get("content", {})
        if not isinstance(content, dict):
            continue

        # Detect byteContent or imageBase64 entries
        if "byteContent" in content:
            img_data = content["byteContent"]
            # Some Bedrock APIs return inline base64 data
            img_links.append({
                "source": "inline_bedrock",
                "url": img_data,  # already data:image/png;base64,...
                "mime_type": "image/png",
                "display_name": "Retrieved Image",
                "caption": content.get("text", "No caption"),
                "context": "Returned directly by Bedrock retrieval",
                "relevance": round(float(score), 3),
            })
            seen_sources.add(img_data[:100])  # avoid duplicates by prefix hash

        # If the result points to an S3 image file directly
        loc = result.get("location", {}).get("s3Location", {})
        uri = loc.get("uri")
        if uri and any(uri.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".svg"]):
            bucket, key = parse_s3_uri(uri)
            if not (bucket and key):
                continue
            if key in seen_sources:
                continue
            seen_sources.add(key)
            img_info = make_presigned(bucket, key)
            img_info["caption"] = content.get("text", "No caption")
            img_info["context"] = "Returned directly by Bedrock retrieval"
            img_info["relevance"] = round(float(score), 3)
            img_links.append(img_info)

        if len(img_links) >= image_limit:
            break

    return img_links


def extract_caption_and_context(structured_data, text_map, max_chars=800):
    """Reconstruct captions and context for figures using structured.json."""
    if not structured_data or "pictures" not in structured_data:
        return {}

    result = {}
    for idx, pic in enumerate(structured_data["pictures"]):
        caption = None
        page_no = None
        fig_num = pic.get("label", "")
        if pic.get("children"):
            for child in pic["children"]:
                ref = child.get("$ref")
                if ref and ref in text_map:
                    t = text_map[ref]
                    if t.get("label") == "caption":
                        caption = t["text"].strip()
                        break
            if not caption:
                for child in pic["children"]:
                    ref = child.get("$ref")
                    if ref and ref in text_map:
                        caption = text_map[ref]["text"].strip()
                        break
        if pic.get("prov"):
            page_no = pic["prov"][0].get("page_no")

        context = extract_surrounding_context(structured_data, page_no, max_chars)
        result[idx] = {
            "caption": f"{fig_num}: {caption}" if caption and fig_num else caption or "No caption",
            "context": context,
        }
    return result


def extract_surrounding_context(structured_data, page_no, max_chars=800):
    """Extract nearby text from same/adjacent pages for figure context."""
    if not structured_data or "texts" not in structured_data:
        return "No context available"
    relevant = [
        t["text"] for t in structured_data["texts"]
        if t.get("prov") and abs(t["prov"][0].get("page_no", -1) - page_no) <= 1
    ]
    joined = " ".join(relevant)[:max_chars]
    return joined if joined else "No context available"


def find_pdf_for_paper(bucket, base_dir, score):
    """Locate the PDF file for a paper."""
    try:
        resp = s3.list_objects_v2(Bucket=bucket, Prefix=base_dir)
        for obj in resp.get("Contents", []):
            if obj["Key"].lower().endswith(".pdf"):
                pdf_info = make_presigned(bucket, obj["Key"])
                pdf_info["relevance"] = round(float(score), 3)
                print(f"[📄] Added PDF {pdf_info['display_name']} (score={pdf_info['relevance']})")
                return pdf_info
    except Exception as e:
        print(f"[!] Could not list {base_dir}: {e}")
    return None


def format_answer(pdf_links):
    """Format textual summary of retrieved PDFs and scores."""
    if not pdf_links:
        return "No documents retrieved."
    lines = [
        f"{i+1}. {pdf['display_name']} (score: {pdf.get('relevance',0):.3f})"
        for i, pdf in enumerate(pdf_links[:5])
    ]
    return "Top retrieved references:\n" + "\n".join(lines)


def run_generation(question: str, retrieved_chunks: list) -> str:
    """Retrieve and generate in one call; return answer text and retrieved refs."""
    response = bedrock_agent.retrieve_and_generate(
        input={"text": question},
        retrieveAndGenerateConfiguration={
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": {
                "knowledgeBaseId": KB_ID,
                "modelArn": MODEL_ARN,
            },
        },
    )

    # save debug dump if needed
    if(debug):
        debug_path = Path(f"bedrock_debug_{datetime.now():%Y%m%d_%H%M%S}.json")
        with open(debug_path, "w", encoding="utf-8") as f:
            json.dump(response, f, indent=2, ensure_ascii=False)
        print(f"[🧠 Saved full Bedrock response to: {debug_path.resolve()}]")

    answer = response.get("output", {}).get("text", "")
    citations = response.get("citations", [])
    retrievals = []
    for c in citations:
        retrievals.extend(c.get("retrievedReferences", []))
    return answer




# -----------------------
# Upload Endpoints
# -----------------------
@app.post("/upload_permanent")
async def upload_permanent(file: UploadFile = File(...)):
    try:
        file_path = UPLOAD_DIR / file.filename
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        from pdf_chunker.new_chunker import extract_with_docling
        print(f"[•] Chunking {file_path} → {OUTPUT_DIR}")
        extract_with_docling(file_path, OUTPUT_DIR)
        print(f"[✓] Finished chunking {file.filename}")

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
        return {"status": "ok", "filename": file.filename, "chunk_count": len(extracted_text.split("\n\n"))}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ============================================================
# ---------------------- BATCH UPLOAD PAGE --------------------
# ============================================================

@app.get("/batch_upload", response_class=HTMLResponse)
def serve_batch_upload_ui():
    """Serve batch upload UI page."""
    return open(Path(__file__).resolve().parent / "batch_upload.html").read()


@app.post("/upload_batch")
async def upload_batch(request: Request, files: list[UploadFile] = File(...)):
    """
    Upload PDFs into tau-papers-N/kb-data/ folders.
    Each bucket's kb-data/ can hold up to 30 paper folders before a new bucket is created.
    """
    try:
        from pdf_chunker.s3_push import get_md5, s3_object_md5, sync_to_s3
        from pdf_chunker.new_chunker import extract_with_docling

        base_name = os.getenv("PDF_BUCKET_BASE", "tau-papers")
        region = os.getenv("AWS_REGION", "us-east-2")
        s3 = boto3.client("s3", region_name=region)

        uploaded, skipped = [], []

        # --- 1️⃣ List all existing tau-papers-N buckets ---
        buckets = [
            b["Name"] for b in s3.list_buckets()["Buckets"]
            if b["Name"].startswith(base_name)
        ]
        buckets.sort(key=lambda b: int(b.replace(base_name + "-", "")))

        # --- 2️⃣ Helper: count subfolders in kb-data/ ---
        def count_kb_data(bucket):
            try:
                resp = s3.list_objects_v2(Bucket=bucket, Prefix="kb-data/", Delimiter="/")
                prefixes = [p["Prefix"] for p in resp.get("CommonPrefixes", [])]
                return len(prefixes)
            except Exception as e:
                print(f"[!] Could not count in {bucket}: {e}")
                return 9999  # assume full if error

        # --- 3️⃣ Helper: pick or create bucket ---
        def get_available_bucket():
            for b in buckets:
                count = count_kb_data(b)
                print(f"[{b}] has {count} folders")
                if count < 30:
                    return b

            # all full → make a new one
            new_index = len(buckets) + 1
            new_bucket = f"{base_name}-{new_index}"
            print(f"[+] Creating new bucket: {new_bucket}")
            s3.create_bucket(
                Bucket=new_bucket,
                CreateBucketConfiguration={"LocationConstraint": region}
            )
            buckets.append(new_bucket)
            return new_bucket

        # --- 4️⃣ Process each uploaded file ---
        for file in files:
            tmp_path = UPLOAD_DIR / file.filename
            with open(tmp_path, "wb") as buf:
                shutil.copyfileobj(file.file, buf)

            md5 = get_md5(tmp_path)
            duplicate_found = False

            # 🔍 check for duplicates across all buckets
            for b in buckets:
                try:
                    resp = s3.list_objects_v2(Bucket=b, Prefix="kb-data/")
                    for obj in resp.get("Contents", []):
                        if obj["Key"].lower().endswith(".pdf"):
                            if s3_object_md5(b, obj["Key"]) == md5:
                                print(f"[⏭] Duplicate found: {file.filename}")
                                skipped.append(file.filename)
                                duplicate_found = True
                                break
                    if duplicate_found:
                        break
                except Exception as e:
                    print(f"[!] Error checking duplicates in {b}: {e}")

            if duplicate_found:
                continue

            # 🧩 Chunk and upload
            extract_with_docling(tmp_path, OUTPUT_DIR)
            target_bucket = get_available_bucket()

            # Upload under kb-data/<paper_name>/
            target_prefix = f"kb-data/{tmp_path.stem}"
            sync_to_s3(OUTPUT_DIR / tmp_path.stem, bucket_name=target_bucket, prefix=target_prefix)
            uploaded.append({"file": file.filename, "bucket": target_bucket})

        return {
            "status": "ok",
            "uploaded": uploaded,
            "skipped": skipped,
            "message": f"Uploaded {len(uploaded)} new, skipped {len(skipped)} duplicates."
        }

    except Exception as e:
        import traceback; traceback.print_exc()
        return {"status": "error", "message": str(e)}


# -----------------------
# Run
# -----------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
