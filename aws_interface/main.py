from fastapi import FastAPI, File, UploadFile, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from dotenv import load_dotenv
import os

# Load environment variables BEFORE importing boto3-dependent modules
load_dotenv(override=True)

import boto3
import re
import mimetypes
from urllib.parse import quote_plus
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
from aws_interface.master_router import run_master_query
from fastapi.staticfiles import StaticFiles



# -----------------------
# Environment & paths
# -----------------------

BASE_DIR = Path(__file__).resolve().parent.parent  # /rag-llm
PDF_CHUNKER_DIR = BASE_DIR / "pdf_chunker"
UPLOAD_DIR = PDF_CHUNKER_DIR / "pdfs"
OUTPUT_DIR = PDF_CHUNKER_DIR / "output"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

CHAT_PASSWORD = os.getenv("CHAT_PASSWORD")
KB_ID = os.getenv("KB_ID")
REGION = os.getenv("AWS_REGION", "us-east-2")
# -----------------------
# Model Registry
# -----------------------

MODEL_ARNS = {
    "Claude Sonnet 4": os.getenv("CLAUDE_SONNET_4_ARN"),
    "Claude Sonnet 4.5": os.getenv("CLAUDE_SONNET_4_5_ARN"),
    "Claude Haiku 4.5": os.getenv("CLAUDE_HAIKU_4_5_ARN"),
    "Claude Opus 4": os.getenv("CLAUDE_OPUS_4_ARN"),
    "Claude Opus 4.1": os.getenv("CLAUDE_OPUS_4_1_ARN"),
    "Claude 3.7 Sonnet": os.getenv("CLAUDE_SONNET_3_7_ARN"),
    "Claude 3.5 Sonnet v2": os.getenv("CLAUDE_SONNET_3_5_V2_ARN"),
    "Claude 3.5 Haiku": os.getenv("CLAUDE_HAIKU_3_5_ARN"),
    "Claude 3 Haiku": os.getenv("CLAUDE_HAIKU_3_ARN"),

    "Nova Premier": os.getenv("NOVA_PREMIER_ARN"),
    "Nova Pro": os.getenv("NOVA_PRO_ARN"),
    "Nova Lite": os.getenv("NOVA_LITE_ARN"),
    "Nova Micro": os.getenv("NOVA_MICRO_ARN"),

    "Llama 3.1 8B": os.getenv("LLAMA_3_1_8B_ARN"),
    "Llama 3.1 70B": os.getenv("LLAMA_3_1_70B_ARN"),
    "Llama 3.1 405B": os.getenv("LLAMA_3_1_405B_ARN"),
    "Llama 3.2 1B": os.getenv("LLAMA_3_2_1B_ARN"),
    "Llama 3.2 3B": os.getenv("LLAMA_3_2_3B_ARN"),
    "Llama 3.2 11B": os.getenv("LLAMA_3_2_11B_ARN"),
    "Llama 3.2 90B": os.getenv("LLAMA_3_2_90B_ARN"),
    "Llama 3.3 70B": os.getenv("LLAMA_3_3_70B_ARN"),
    "Llama 4 Scout 17B": os.getenv("LLAMA_4_SCOUT_17B_ARN"),
    "Llama 4 Maverick 17B": os.getenv("LLAMA_4_MAVERICK_17B_ARN"),

    "DeepSeek R1": os.getenv("DEEPSEEK_R1_ARN"),
}

def get_model_arn(model_name: str) -> str:
    arn = MODEL_ARNS.get(model_name)
    if not arn:
        raise ValueError(f"Unknown model: {model_name}")
    return arn

debug=False

bedrock_agent = boto3.client("bedrock-agent-runtime", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)
app = FastAPI()

# In-memory state
session_state = {}

#Favicon 
BASE_DIR = Path(__file__).parent

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # or list your actual frontend origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


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


def resolve_paper_root(key: str, base_dir: str) -> str:
    """
    Resolve the S3 chunk key to the paper root directory.
    Bedrock returns chunk paths like .../chunks/text/chunk_001.txt or .../text/page_1.txt.
    Images, structured.json, and PDFs live at the paper root (parent of text/chunks).
    """
    # new_chunker (Docling): .../paper-name/chunks/text/chunk_001.txt
    if "/chunks/text" in key:
        return key.split("/chunks/text")[0].rstrip("/")
    # chunks in subdir: .../paper-name/chunks/...
    if "/chunks/" in key:
        return key.split("/chunks/")[0].rstrip("/")
    # old chunker (PyMuPDF): .../paper-name/text/page_1.txt
    if "/text/" in key:
        return key.split("/text/")[0].rstrip("/")
    if key.endswith("/text"):
        return key[:-5].rstrip("/")
    # Walk up from base_dir until we reach a plausible paper root (has output/ or similar)
    parts = base_dir.rstrip("/").split("/")
    for i in range(len(parts) - 1, 0, -1):
        candidate = "/".join(parts[: i + 1])
        if "output" in candidate or "kb-data" in candidate:
            return candidate
    return base_dir

@app.post("/delete_s3_object")
async def delete_s3_object(request: Request):
    data = await request.json()
    url = data.get("url")
    if not url:
        return {"error": "Missing URL"}

    try:
        subprocess.run(["python3", "pdf_chunker/s3_delete.py", url], check=True)
        return {"message": f"Deleted {url} successfully."}
    except subprocess.CalledProcessError as e:
        return {"error": f"Deletion failed: {e}"}


def make_presigned(bucket: str, key: str):
    presigned_url = s3.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=3600
    )
    mime_type, _ = mimetypes.guess_type(key)
    if not mime_type:
        mime_type = "application/octet-stream"
    name = os.path.splitext(os.path.basename(key))[0]
    display_name = name.replace("_", " ").replace("-", " ").title().strip()
    # S3 console URL to view object in AWS console
    encoded_key = quote_plus(key)
    s3_console_url = (
        f"https://{REGION}.console.aws.amazon.com/s3/object/{bucket}"
        f"?region={REGION}&prefix={encoded_key}"
    )
    print(presigned_url)
    return {
        "source": f"s3://{bucket}/{key}",
        "url": presigned_url,
        "mime_type": mime_type,
        "display_name": display_name,
        "s3_console_url": s3_console_url,
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
        result_limit = int(query.get("result_limit", 10))
        score_threshold = float(query.get("score_threshold", 0.7))


        # Janky solution
        #question = "You are a topical expert language model that is a domain expert in the field that you have references to. All of your answers must be sent in hierarchical structures and bullet points to make them easily digestible. It is important that you separate all of your different answers and points with newlines. Now answer the following question: " + query["question"]
        question = query["question"]

        #question = query["question"]
        image_limit = int(query.get("image_limit", 8))

        # 1️⃣ Build prompt context
        combined_prompt = build_context(state, question, query)

        """# 2️⃣ Run Bedrock retrieval
        response = run_retrieval(combined_prompt, result_limit, score_threshold)
        retrievals = response.get("retrievalResults", [])
        model_name = query.get("model_name", "Claude Sonnet 4")
        model_arn = get_model_arn(model_name)
        generated_answer = run_generation(combined_prompt, retrievals, model_arn, result_limit, score_threshold) """

        # 2️⃣ Route + retrieve + generate (router decides KB)
        model_name = query.get("model_name", "Claude Sonnet 4")
        model_arn = get_model_arn(model_name)

        router_result = run_master_query(
            question=combined_prompt,
            model_arn=model_arn,
            result_limit=result_limit,
        )

        generated_answer = router_result["answer"]

        # Extract retrievals for downstream image/PDF processing
        retrievals = []
        for c in router_result.get("citations", []):
            retrievals.extend(c.get("retrievedReferences", []))

        # retrieve_and_generate citations don't include scores; fetch from retrieve() API
        kb_id = router_result.get("kb_id")
        score_map = {}
        if kb_id:
            score_map = build_score_map_from_retrieve(
                kb_id, combined_prompt, num_results=min(50, result_limit * 5)
            )

        # 3️⃣ Process each retrieved paper
        all_links, pdf_links, total_images = [], [], 0
        processed_folders = set()

        for r_idx, result in enumerate(retrievals):
            s3_uri = result.get("location", {}).get("s3Location", {}).get("uri")
            if not s3_uri:
                continue

            bucket, key = parse_s3_uri(s3_uri)
            if not (bucket and key):
                continue

            base_dir = "/".join(key.split("/")[:-1])
            paper_root = resolve_paper_root(key, base_dir)
            # Citations from retrieve_and_generate don't include score; look up from retrieve() results
            score = score_map.get((bucket, paper_root), result.get("score", 0.0))
            if paper_root in processed_folders:
                continue
            processed_folders.add(paper_root)

            # Process one full paper (structured.json + figures + pdf)
            links, pdfs, img_count = process_paper(bucket, paper_root, base_dir, score, image_limit - total_images)
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

MATH_INSTRUCTION = (
    "When you need to show mathematical expressions, use LaTeX format: "
    "inline math with $...$ and display/block math with $$...$$. "
    "Example: $E = mc^2$ or $$\\frac{\\partial f}{\\partial x}$$. "
    "Do not use \\\\(...\\\\) or \\\\[...\\\\]; use $ and $$ only.\n\n"
)


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
        f"{MATH_INSTRUCTION}{chat_context}\n\nRelevant PDF content:\n{pdf_text}"
        f"\n\nSelected Figures for Reference:{image_context}\n\nUser: {question}"
    )

def build_score_map_from_retrieve(kb_id: str, question: str, num_results: int = 30) -> dict:
    """
    Call Bedrock retrieve() to get relevance scores. retrieve_and_generate citations
    do not include scores, so we fetch them separately and map paper_root -> score.
    """
    try:
        response = bedrock_agent.retrieve(
            knowledgeBaseId=kb_id,
            retrievalQuery={"text": question},
            retrievalConfiguration={
                "vectorSearchConfiguration": {"numberOfResults": num_results}
            },
        )
        score_map = {}
        for r in response.get("retrievalResults", []):
            uri = r.get("location", {}).get("s3Location", {}).get("uri")
            score = r.get("score", 0.0)
            if not uri:
                continue
            bucket, key = parse_s3_uri(uri)
            if not (bucket and key):
                continue
            base_dir = "/".join(key.split("/")[:-1])
            paper_root = resolve_paper_root(key, base_dir)
            # Use max score when multiple chunks from same paper
            key = (bucket, paper_root)
            score_map[key] = max(score_map.get(key, 0.0), float(score))
        return score_map
    except Exception as e:
        print(f"[!] retrieve() failed for score map: {e}")
        return {}


def run_retrieval(question, num_results=10, threshold=0.7):
    """
    Retrieve chunks from the knowledge base for the given query.
    Applies client-side score filtering instead of invalid Bedrock filters.
    """
    response = bedrock_agent.retrieve(
        knowledgeBaseId=KB_ID,
        retrievalQuery={"text": question},
        retrievalConfiguration={
            "vectorSearchConfiguration": {
                "numberOfResults": num_results
            }
        },
    )

    # Save debug output if needed
    if debug:
        debug_path = Path(f"bedrock_debug_{datetime.now():%Y%m%d_%H%M%S}.json")
        with open(debug_path, "w", encoding="utf-8") as f:
            json.dump(response, f, indent=2, ensure_ascii=False)
        print(f"[🧠 Saved full Bedrock response to: {debug_path.resolve()}]")

    # Filter results by score locally
    results = response.get("retrievalResults", [])
    filtered = [r for r in results if r.get("score", 0.0) >= threshold]

    for i, r in enumerate(filtered):
        src = r.get("metadata", {}).get("sourceId") or r.get("metadata", {}).get("documentId")
        print(f"[{i}] {src} (score={r.get('score', 0.0):.3f})")


    print(f"[ℹ️ Retrieved {len(results)} results, kept {len(filtered)} after threshold ≥ {threshold}]")
    response["retrievalResults"] = filtered
    return response

def process_paper(bucket, paper_root, base_dir, score, remaining_slots):
    """Handles one paper’s structured.json, figures, and PDF extraction."""
    links, pdf_links, total_added = [], [], 0

    structured_data = load_structured_json(bucket, paper_root, base_dir)
    text_map = {t["self_ref"]: t for t in structured_data.get("texts", [])} if structured_data else {}
    pic_map = extract_caption_and_context(structured_data, text_map)

    valid_images = find_images_for_paper(bucket, paper_root, base_dir)
    if not valid_images:
        print(f"[📂] No valid images under {paper_root} (resolved from {base_dir})")

    for idx, key in enumerate(valid_images[:remaining_slots]):
        img_info = make_presigned(bucket, key)
        img_info.update(pic_map.get(idx, {"caption": "No caption", "context": "No context"}))
        img_info["relevance"] = round(float(score), 3)
        links.append(img_info)
        total_added += 1
        print(f"[🖼] Added image: {Path(key).name}")

    # Add one PDF per paper
    pdf_info = find_pdf_for_paper(bucket, paper_root, base_dir, score)
    if pdf_info:
        pdf_links.append(pdf_info)

    return links, pdf_links, total_added


def load_structured_json(bucket, paper_root, base_dir):
    """Loads structured.json for the given paper, trying multiple common paths."""
    def _norm(p):
        return re.sub(r"/+", "/", p.strip("/"))

    # When base_dir is .../text, structured.json lives in the parent (dir before /text)
    parent_of_text = base_dir.rsplit("/text", 1)[0].rstrip("/") if "/text" in base_dir else None

    candidates = list(dict.fromkeys([
        f"{_norm(paper_root)}/structured.json",
        f"{paper_root}/structured.json",
        f"{paper_root.rstrip('/')}/structured.json",
        f"{parent_of_text}/structured.json" if parent_of_text else "",
        f"{_norm(parent_of_text)}/structured.json" if parent_of_text else "",
        f"{_norm(base_dir)}/structured.json",
        f"{base_dir}/structured.json",
    ]))
    candidates = [c for c in candidates if c]
    last_err = None
    for key in candidates:
        try:
            obj = s3.get_object(Bucket=bucket, Key=key)
            data = json.loads(obj["Body"].read())
            print(f"[📄] Loaded structured.json from {key}")
            return data
        except Exception as e:
            last_err = e
            continue

    # Fallback: list S3 under paper_root (or base_dir) to find structured.json
    # Must try raw path too - S3 keys can have double slashes (e.g. kb-data/output//PaperName)
    for try_prefix in [p for p in [paper_root, _norm(paper_root), parent_of_text, base_dir, _norm(base_dir)] if p]:
        prefix = try_prefix.rstrip("/") + "/"
        try:
            resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=500)
            for obj in resp.get("Contents", []):
                k = obj["Key"]
                if k.endswith("structured.json"):
                    try:
                        data = json.loads(
                            s3.get_object(Bucket=bucket, Key=k)["Body"].read()
                        )
                        print(f"[📄] Loaded structured.json from {k} (list fallback)")
                        return data
                    except Exception:
                        continue
        except Exception:
            pass

    err_msg = str(last_err) if last_err else "unknown"
    print(f"[!] No structured.json found for paper_root={paper_root}, base_dir={base_dir}")
    print(f"    bucket={bucket} last_error={err_msg}")
    return {}


def find_images_for_paper(bucket, paper_root, base_dir):
    """Collects all valid image keys across several common paths.
    Handles both new_chunker (Docling) and old chunker (PyMuPDF) layouts.
    """
    paper_name = Path(paper_root).name
    prefixes = [
        # new_chunker (Docling): paper_root/images/
        f"{paper_root}/images/",
        # old chunker (PyMuPDF): paper_root/images/, paper_root/figures/
        f"{paper_root}/figures/",
        # nested output layouts
        f"{paper_root}/output/{paper_name}/images/",
        f"{paper_root}/output/images/",
        f"{paper_root}/{paper_name}/images/",
        # fine_grain_chunker: chunks/images/
        f"{paper_root}/chunks/images/",
        # fallback: base_dir (e.g. when chunk came from /text/)
        f"{base_dir}/images/",
        f"{base_dir}/figures/",
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


def find_pdf_for_paper(bucket, paper_root, base_dir, score):
    """Locate the PDF file for a paper. Searches paper root and common subpaths."""
    search_prefixes = [paper_root, base_dir]
    for prefix in search_prefixes:
        try:
            resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
            for obj in resp.get("Contents", []):
                k = obj["Key"]
                if k.lower().endswith(".pdf"):
                    pdf_info = make_presigned(bucket, k)
                    pdf_info["relevance"] = round(float(score), 3)
                    print(f"[📄] Added PDF {pdf_info['display_name']} (score={pdf_info['relevance']})")
                    return pdf_info
        except Exception as e:
            print(f"[!] Could not list {prefix}: {e}")
    return None


def format_answer(pdf_links):
    """Format textual summary of ALL retrieved PDFs above threshold."""
    if not pdf_links:
        return "No documents retrieved."

    lines = [
        f"{i+1}. {pdf['display_name']} (score: {pdf.get('relevance',0):.3f})"
        for i, pdf in enumerate(pdf_links)
    ]

    return "Retrieved references (meeting threshold):\n" + "\n".join(lines)



def run_generation(question: str, retrieved_chunks: list, model_arn: str,
                   result_limit: int = 10, score_threshold: float = 0.7) -> str:
    """
    Generate a response using Bedrock's retrieve_and_generate API with 
    optional client-side control over number of results and score threshold.
    """
    response = bedrock_agent.retrieve_and_generate(
        input={"text": question},
        retrieveAndGenerateConfiguration={
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": {
                "knowledgeBaseId": KB_ID,
                "modelArn": model_arn,
                "retrievalConfiguration": {
                    "vectorSearchConfiguration": {
                        "numberOfResults": result_limit
                    }
                },
            },
        },
    )

    # Save debug info
    if debug:
        debug_path = Path(f"bedrock_debug_{datetime.now():%Y%m%d_%H%M%S}.json")
        with open(debug_path, "w", encoding="utf-8") as f:
            json.dump(response, f, indent=2, ensure_ascii=False)
        print(f"[🧠 Saved full Bedrock response to: {debug_path.resolve()}]")

    # Extract text and citations
    answer = response.get("output", {}).get("text", "")
    citations = response.get("citations", [])
    retrievals = []
    for c in citations:
        retrievals.extend(c.get("retrievedReferences", []))

    # Local post-filtering for completeness (if citations have scores)
    filtered = [r for r in retrievals if r.get("score", 0.0) >= score_threshold]
    response["filteredReferences"] = filtered

    print(f"[ℹ️ Generated answer with {len(filtered)} filtered references (≥ {score_threshold})]")
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
            if b["Name"].startswith(os.getenv("PDF_BUCKET_BASE", "tau-papers"))
        ]
        print("[🪣] Found candidate buckets:")
        for b in buckets:
            print(f"   - {b}")


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

# ARXIV SCRAPER
from arxivscraper import Scraper

from pdf_chunker.arxiv_download import compile_arxiv_query


@app.get("/arxiv", response_class=HTMLResponse)
def serve_arxiv_ui():
    return open(Path(__file__).resolve().parent / "arxiv_scraper.html").read()


@app.get("/arxiv", response_class=HTMLResponse)
def serve_arxiv_ui():
    return open(Path(__file__).resolve().parent / "arxiv_scraper.html").read()


@app.post("/fetch_arxiv_papers")
async def fetch_arxiv_papers(request: Request):
    import requests, feedparser, re
    from pathlib import Path
    from urllib.parse import quote_plus

    try:
        params = await request.json()
        limit = int(params.get("limit", 10))

        # Compile Boolean query → arXiv syntax
        compiled_query = compile_arxiv_query(params)
        encoded_q = quote_plus(compiled_query)

        print("[🔎] Fetching arXiv papers with query:")
        print(f"     {compiled_query}")
        print(f"     limit={limit}")

        url = (
            "https://export.arxiv.org/api/query"
            f"?search_query={encoded_q}"
            f"&start=0&max_results={limit}"
        )

        feed = feedparser.parse(url)

        download_dir = (
            Path(__file__).resolve().parent.parent
            / "pdf_chunker"
            / "pdfs"
        )
        download_dir.mkdir(parents=True, exist_ok=True)

        papers = []

        for entry in feed.entries:
            title = entry.title.strip().replace("\n", " ")
            authors = ", ".join(a.name for a in entry.authors)
            abstract = entry.summary.strip().replace("\n", " ")
            arxiv_id = entry.id.split("/abs/")[-1]
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

            safe_name = (
                re.sub(r"[^\w\s-]", "", title)
                .strip()
                .replace(" ", "_")
                + ".pdf"
            )
            pdf_path = download_dir / safe_name

            # Download PDF
            try:
                resp = requests.get(pdf_url, timeout=20)
                if resp.status_code == 200:
                    with open(pdf_path, "wb") as f:
                        f.write(resp.content)
                    print(f"[📄] Saved {pdf_path.name}")
                else:
                    print(f"[!] Failed to fetch {pdf_url}: HTTP {resp.status_code}")
            except Exception as e:
                print(f"[!] Error fetching {pdf_url}: {e}")

            papers.append({
                "title": title,
                "authors": authors,
                "abstract": abstract,
                "pdf_url": pdf_url,
            })

        return {
            "papers": papers,
            "download_dir": str(download_dir),
            "compiled_query": compiled_query,
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": str(e)}



@app.post("/download_arxiv_pdfs")
async def download_arxiv_pdfs(request: Request):
    import pandas as pd
    import requests
    from arxivscraper import Scraper

    try:
        params = await request.json()
        query = params.get("query")
        category = params.get("category") or ""
        limit = int(params.get("limit", 10))

        print(f"[🔎] Scraping arXiv for query='{query}', category='{category}', limit={limit}")

        scraper = Scraper(category=category, filters={"title": query})
        output = scraper.scrape()
        if not output:
            return {"error": "No papers found."}

        # Convert to dataframe
        cols = ("id", "title", "categories", "abstract")
        df = pd.DataFrame(output, columns=cols)
        results = df.head(limit)

        # Set up download folder
        download_dir = Path(__file__).resolve().parent.parent / "pdf_chunker" / "pdfs"
        download_dir.mkdir(parents=True, exist_ok=True)

        downloaded = 0
        for _, row in results.iterrows():
            arxiv_id = row["id"]
            title = row["title"]
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

            safe_name = re.sub(r"[^\w\s-]", "", title).strip().replace(" ", "_")
            pdf_path = download_dir / f"{safe_name}.pdf"

            print(f"[⬇️] Downloading {pdf_url}")
            try:
                resp = requests.get(pdf_url, timeout=20)
                if resp.status_code == 200:
                    with open(pdf_path, "wb") as f:
                        f.write(resp.content)
                    downloaded += 1
                    print(f"[📄] Saved {pdf_path.name}")
                else:
                    print(f"[!] Failed: HTTP {resp.status_code} for {arxiv_id}")
            except Exception as e:
                print(f"[!] Error downloading {arxiv_id}: {e}")

        print(f"[✅] Downloaded {downloaded} PDFs to {download_dir}")
        return {"downloaded": downloaded, "folder": str(download_dir)}
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"error": str(e)}
    

@app.post("/master_query")
async def master_query(request: Request):
    data = await request.json()
    question = data.get("question")

    if not question:
        return {"error": "Missing question"}

    # Use a cheap / fast model for routing + answers
    model_arn = get_model_arn("Claude Haiku 4.5")

    result = run_master_query(question, model_arn)
    return result


# -----------------------
# Run
# -----------------------
if __name__ == "__main__":
    # NOTE: For --reload to work, uvicorn needs an import string (not the app object).
    # This allows running via: python3 -m aws_interface.main
    uvicorn.run("aws_interface.main:app", host="0.0.0.0", port=8000, reload=True)
