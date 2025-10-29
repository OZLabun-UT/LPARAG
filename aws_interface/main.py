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
import json
import subprocess
from pdf_chunker.new_chunker import extract_text_for_session
from io import BytesIO
from PIL import Image

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

bedrock_agent = boto3.client("bedrock-agent-runtime", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)
app = FastAPI()

# In-memory state
session_state = {}

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

        # ---- Context ----
        chat_context = "\n".join(
            [f"User: {h['user']}\nAssistant: {h['assistant']}" for h in state["history"][-3:]]
        )
        pdf_text = "\n\n".join([pdf["text"][:5000] for pdf in state["temp_pdfs"]])

        selected_images = query.get("selected_images", [])
        image_context = ""
        if selected_images:
            for i, img in enumerate(selected_images, 1):
                image_context += (
                    f"\n\n[Image {i}]\n"
                    f"Caption: {img.get('caption')}\n"
                    f"Context: {img.get('context')}\n"
                    f"Source: {img.get('source')}\n"
                    f"NOTE: User has selected this image for reference. "
                    f"Use the caption and context to answer questions about this figure."
                )

        combined_prompt = (
            f"{chat_context}\n\nRelevant PDF content:\n{pdf_text}"
            f"\n\nSelected Figures for Reference:{image_context}\n\nUser: {question}"
        )

        # ---- Query Bedrock ----
        response = bedrock_agent.retrieve_and_generate(
            input={"text": combined_prompt},
            retrieveAndGenerateConfiguration={
                "knowledgeBaseConfiguration": {"knowledgeBaseId": KB_ID, "modelArn": MODEL_ARN},
                "type": "KNOWLEDGE_BASE",
            },
        )

        answer = response["output"]["text"]
        citations = response.get("citations", [])
        links, pdf_links, processed_folders = [], [], {}
        total_images_collected = 0

        # ---- Utility helpers ----
        def get_base_dir_from_key(key: str):
            """
            Determine the base directory for a given S3 key.
            Compatible with both old and new folder layouts.
            """
            parts = key.split("/")
            # Handle common patterns
            if "chunks" in parts:
                idx = parts.index("chunks")
                return "/".join(parts[:idx])
            if "structured.json" in parts:
                return "/".join(parts[:-1])
            if "output" in parts:
                idx = parts.index("output")
                return "/".join(parts[:idx])
            if "images" in parts:
                idx = parts.index("images")
                return "/".join(parts[:idx])
            return "/".join(parts[:3])


        def find_all_images(bucket, prefix):
            """
            List all image objects (.png, .jpg, .jpeg, .svg) under a given S3 prefix.
            Includes robust pagination to ensure all images are returned.
            """
            found = []
            continuation_token = None
            try:
                while True:
                    kwargs = {"Bucket": bucket, "Prefix": prefix}
                    if continuation_token:
                        kwargs["ContinuationToken"] = continuation_token
                    resp = s3.list_objects_v2(**kwargs)
                    for obj in resp.get("Contents", []):
                        key = obj["Key"]
                        if key.lower().endswith((".png", ".jpg", ".jpeg", ".svg")):
                            found.append(key)
                    if resp.get("IsTruncated"):
                        continuation_token = resp.get("NextContinuationToken")
                    else:
                        break
            except Exception as e:
                print(f"[!] Error listing {prefix}: {e}")
            return found


        def try_list(prefix):
            try:
                resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
                return [obj["Key"] for obj in resp.get("Contents", [])]
            except Exception:
                return []

        def extract_surrounding_context(structured_data, page_no, max_chars=800):
            """Extract richer context from the same page and adjacent pages."""
            if not structured_data or "texts" not in structured_data:
                return "No context available"
            
            # Collect texts from target page and adjacent pages
            relevant_texts = []
            for text_obj in structured_data["texts"]:
                if not text_obj.get("prov"):
                    continue
                text_page = text_obj["prov"][0].get("page_no")
                # Include same page and ±1 pages
                if text_page is not None and abs(text_page - page_no) <= 1:
                    relevant_texts.append({
                        "page": text_page,
                        "text": text_obj["text"],
                        "label": text_obj.get("label", "")
                    })
            
            # Sort by page number
            relevant_texts.sort(key=lambda x: x["page"])
            
            # Build context prioritizing section headers and nearby text
            context_parts = []
            total_chars = 0
            
            # First pass: add section headers
            for t in relevant_texts:
                if t["label"] in ["section_header", "title"] and total_chars < max_chars:
                    context_parts.append(f"[{t['label'].upper()}] {t['text']}")
                    total_chars += len(t['text'])
            
            # Second pass: add regular text from the same page
            for t in relevant_texts:
                if t["page"] == page_no and t["label"] not in ["section_header", "title"] and total_chars < max_chars:
                    text_snippet = t["text"][:max_chars - total_chars]
                    context_parts.append(text_snippet)
                    total_chars += len(text_snippet)
                    if total_chars >= max_chars:
                        break
            
            return " ".join(context_parts) if context_parts else "No context available"

        # -------- MAIN LOOP --------
        for citation in citations:
            for ref in citation.get("retrievedReferences", []):
                s3_uri = ref["location"]["s3Location"]["uri"]
                bucket, key = parse_s3_uri(s3_uri)
                if not (bucket and key):
                    continue

                base_dir = get_base_dir_from_key(key)
                if base_dir in processed_folders:
                    continue
                processed_folders[base_dir] = True

                # ---- Add cited reference ----
                if not (key.lower().endswith(".json") or key.lower().endswith(".txt")):
                    links.append(make_presigned(bucket, key))

                # ---- Load structured.json ----
                structured_key = f"{base_dir}/structured.json"
                structured_data = None
                try:
                    obj = s3.get_object(Bucket=bucket, Key=structured_key)
                    structured_data = json.loads(obj["Body"].read())
                    print(f"[📄] Loaded structured.json for {base_dir}")
                except Exception as e:
                    print(f"[!] No structured.json found for {base_dir}: {e}")

                text_map = {}
                if structured_data and "texts" in structured_data:
                    text_map = {t["self_ref"]: t for t in structured_data["texts"]}

                # ---- Find ALL images first ----
                paper_name = Path(base_dir).name
                possible_prefixes = [
                    f"{base_dir}/output/{paper_name}/images/",
                    f"{base_dir}/output/images/",
                    f"{base_dir}/{paper_name}/images/",
                    f"{base_dir}/images/",
                ]
                all_image_keys = []
                for prefix in possible_prefixes:
                    all_image_keys.extend(find_all_images(bucket, prefix))

                # ---- Filter images by size ----
                valid_images = []
                for k in sorted(set(all_image_keys)):
                    if is_large_enough(bucket, k, min_size=90):  # Lower threshold back to 90
                        valid_images.append(k)
                    else:
                        print(f"[⏭] Skipping small image: {k}")

                print(f"[📂] Found {len(valid_images)} valid images under {base_dir}")

                # ---- Build picture map with enhanced captions ----
                pic_ref_map = {}
                if structured_data and "pictures" in structured_data:
                    for pic in structured_data["pictures"]:
                        caption = ""
                        page_no = None
                        figure_number = ""

                        # Extract figure number
                        if pic.get("label"):
                            figure_number = pic["label"]

                        # Try caption-labeled text
                        if pic.get("children"):
                            for child in pic["children"]:
                                ref = child.get("$ref")
                                if ref and ref in text_map:
                                    child_obj = text_map[ref]
                                    if child_obj.get("label") == "caption":
                                        caption = child_obj["text"].strip()
                                        break

                        # Fallback: first child text
                        if not caption and pic.get("children"):
                            for child in pic["children"]:
                                ref = child.get("$ref")
                                if ref and ref in text_map:
                                    caption = text_map[ref]["text"].strip()
                                    break

                        if pic.get("prov"):
                            page_no = pic["prov"][0].get("page_no", None)

                        pic_ref_map[pic["self_ref"]] = {
                            "caption": caption,
                            "page_no": page_no,
                            "figure_number": figure_number
                        }

                # ---- Calculate how many images we can add from this paper ----
                remaining_slots = image_limit - total_images_collected
                images_to_add = valid_images[:remaining_slots]

                # ---- Add image objects with enhanced metadata ----
                for idx, k in enumerate(images_to_add):
                    img_info = make_presigned(bucket, k)
                    img_info["caption"] = "No caption found"
                    img_info["context"] = "No context available"
                    img_info["relevance"] = None

                    # Match with structured.json info if available
                    matched_ref = list(pic_ref_map.keys())[idx] if idx < len(pic_ref_map) else None
                    if matched_ref and matched_ref in pic_ref_map:
                        data = pic_ref_map[matched_ref]
                        caption = data["caption"]
                        page_no = data["page_no"]
                        figure_number = data["figure_number"]

                        # Caption formatting
                        if figure_number and caption:
                            img_info["caption"] = f"{figure_number}: {caption}"
                        elif caption:
                            img_info["caption"] = caption
                        elif figure_number:
                            img_info["caption"] = figure_number
                        else:
                            img_info["caption"] = img_info["display_name"]

                        # Add context if possible
                        if page_no is not None and structured_data:
                            img_info["context"] = extract_surrounding_context(
                                structured_data, page_no, max_chars=800
                            )

                    print(f"[🖼] Added image: {Path(k).name}")
                    links.append(img_info)
                    total_images_collected += 1

                    # Add only one PDF per paper
                    if total_images_collected == 1:
                        for k in try_list(f"{base_dir}/"):
                            if k.lower().endswith(".pdf"):
                                pdf_info = make_presigned(bucket, k)
                                pdf_info["relevance"] = None
                                pdf_links.append(pdf_info)
                                print(f"[📄] Added PDF: {k}")
                                break

                # mark folder processed
                processed_folders[base_dir] = True

                # ---- Continue until limit reached ----
                if total_images_collected >= image_limit:
                    continue


        print(f"[✓] Collected {total_images_collected} images (limit: {image_limit})")

        # ---- Save session ----
        state["history"].append({"user": question, "assistant": answer})
        state["citations"].extend(citations)

        return {
            "session_id": session_id,
            "answer": answer,
            "documents": links,
            "pdfs": pdf_links,
            "citations": citations,
        }

    except Exception as e:
        print(f"[❌] Query failed: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}



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

# -----------------------
# Run
# -----------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
