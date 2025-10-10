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

from pdf_chunker.new_chunker import extract_text_for_session


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
@app.get("/", response_class=HTMLResponse)
def serve_ui():
    return r"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8" />
      <title>LWFA Chat</title>
      <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Orbitron:wght@600;800&display=swap" rel="stylesheet">
      <style>
        :root {
          --bg:#f5f7fa; --card-bg:#ffffffcc; --accent-bg:#eef2ff;
          --primary:#2563eb; --accent:#7c3aed;
          --text:#111827; --subtext:#4b5563;
          --border:#e5e7eb; --radius:12px; --shadow:0 2px 8px rgba(0,0,0,0.08);
        }
        [data-theme="dark"] {
          --bg:#0d1117; --card-bg:#1e2531cc; --accent-bg:#222a35;
          --primary:#60a5fa; --accent:#a78bfa;
          --text:#f3f4f6; --subtext:#9ca3af;
          --border:#374151; --shadow:0 4px 16px rgba(0,0,0,0.5);
        }
        body {
          font-family:'Inter',sans-serif;
          background:var(--bg); color:var(--text);
          margin:0; min-height:100vh;
          display:flex; flex-direction:column; align-items:center; justify-content:center;
          padding:1.5rem; transition:background .3s,color .3s;
        }
        header{display:flex;justify-content:center;align-items:center;width:100%;margin-bottom:1rem;}
        h1{
          font-family:'Orbitron',sans-serif;font-weight:800;font-size:2.3rem;
          background:linear-gradient(90deg,var(--primary),var(--accent));
          -webkit-background-clip:text;-webkit-text-fill-color:transparent;
          letter-spacing:.05em;margin:0;text-align:center;
        }
        .toggle{position:absolute;top:1rem;right:1.5rem;background:none;border:none;
                font-size:1.3rem;color:var(--accent);cursor:pointer;transition:transform .2s;}
        .toggle:hover{transform:rotate(15deg);}
        main{width:100%;max-width:1200px;display:flex;flex-direction:column;align-items:center;gap:1rem;}
        #chat{
          background:var(--card-bg);border:1px solid var(--border);border-radius:var(--radius);
          box-shadow:var(--shadow);width:100%;height:75vh;overflow-y:auto;
          padding:1.2rem 1.5rem;backdrop-filter:blur(10px);
        }
        .user-msg,.assistant-msg{margin:.6rem 0;padding:.8rem 1rem;border-radius:10px;max-width:85%;}
        .user-msg{background:linear-gradient(135deg,var(--primary),var(--accent));color:#fff;margin-left:auto;text-align:right;}
        .assistant-msg{background:var(--accent-bg);color:var(--text);margin-right:auto;}
        .input-row{
          display:flex;align-items:center;justify-content:center;
          background:var(--card-bg);border:1px solid var(--border);
          border-radius:var(--radius);box-shadow:var(--shadow);
          width:100%;padding:.8rem 1rem;gap:.6rem;backdrop-filter:blur(10px);
        }
        input[type=text],input[type=number]{
          background:var(--accent-bg);color:var(--text);border:1px solid var(--border);
          border-radius:8px;padding:.6rem 1rem;font-size:1rem;transition:border .2s;
        }
        input[type=text]{flex:1;} input[type=number]{width:60px;text-align:center;}
        input:focus{border-color:var(--accent);outline:none;}
        button{
          padding:.6rem 1rem;background:linear-gradient(135deg,var(--accent),var(--primary));
          border:none;border-radius:8px;color:white;font-weight:600;cursor:pointer;
          transition:transform .2s,box-shadow .2s;
        }
        button:hover{transform:translateY(-1px);box-shadow:0 0 8px var(--accent);}
        .upload-toolbar{display:flex;justify-content:center;align-items:center;gap:1rem;margin-top:.3rem;}
        .upload-btn{
          background:var(--accent-bg);color:var(--text);border:1px solid var(--border);
          border-radius:8px;padding:.45rem .9rem;font-size:.9rem;cursor:pointer;
          transition:all .2s;display:flex;align-items:center;gap:.4rem;
        }
        .upload-btn:hover{background:var(--accent);color:white;transform:translateY(-1px);}
        .hidden-input{display:none;}
        .figure-grid{
          display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));
          gap:1rem;margin-top:1rem;
        }
        .figure-card{
          position:relative;background:var(--accent-bg);border:1px solid var(--border);
          border-radius:10px;overflow:hidden;cursor:pointer;
          transition:transform .2s,box-shadow .2s;height:230px;display:flex;flex-direction:column;
        }
        .figure-card:hover{transform:scale(1.03);box-shadow:0 0 12px var(--accent);}
        .figure-card img{width:100%;height:100%;object-fit:cover;flex-grow:1;}
        .figure-overlay{
          position:absolute;bottom:0;left:0;right:0;
          background:rgba(0,0,0,0.55);color:#fff;font-size:.85rem;
          text-align:center;padding:.3rem;opacity:0;transition:opacity .2s;
        }
        .figure-card:hover .figure-overlay{opacity:1;}
        #loading,#uploading{display:none;align-items:center;justify-content:center;gap:10px;color:var(--accent);font-weight:500;}
        .loader{width:18px;height:18px;border:3px solid var(--border);border-top:3px solid var(--accent);
                border-radius:50%;animation:spin .8s linear infinite;}
        @keyframes spin{0%{transform:rotate(0)}100%{transform:rotate(360deg)}}
        .modal{
          display:none;position:fixed;z-index:1000;left:0;top:0;width:100%;height:100%;
          background:rgba(0,0,0,0.7);justify-content:center;align-items:center;
        }
        .modal-content{
          background:var(--card-bg);border-radius:var(--radius);
          width:80%;max-width:950px;max-height:85vh;box-shadow:var(--shadow);
          display:flex;flex-direction:column;overflow:hidden;animation:popin .25s ease;
        }
        @keyframes popin{from{opacity:0;transform:scale(.95);}to{opacity:1;transform:scale(1);}}
        .modal img{width:100%;max-height:45vh;object-fit:contain;border-bottom:1px solid var(--border);}
        .modal-body{flex:1;overflow-y:auto;padding:1rem;}
        .modal h3{color:var(--accent);font-family:'Inter',sans-serif;margin-bottom:.4rem;}
        .modal p{color:var(--subtext);font-size:.95rem;line-height:1.5;}
        .close-btn{align-self:flex-end;margin:.8rem 1rem 0 0;background:var(--accent);
                   color:white;border:none;border-radius:6px;padding:.4rem .8rem;cursor:pointer;}
        .close-btn:hover{background:var(--primary);}
      </style>
    </head>
    <body>
      <button id="themeToggle" class="toggle" title="Toggle theme">🌙</button>
      <header><h1>Knowledge Base Chat</h1></header>
      <main>
        <div id="loading"><div class="loader"></div><span>Thinking...</span></div>
        <div id="uploading"><div class="loader"></div><span>Uploading...</span></div>
        <div id="chat"></div>
        <div class="input-row">
          <input type="text" id="question" placeholder="Ask your question..." />
          <input type="number" id="imgLimit" min="1" max="20" value="8" />
          <button onclick="sendMessage()">Send</button>
        </div>
        <div class="upload-toolbar">
          <label class="upload-btn" for="permFileInput">📁 Permanent Upload</label>
          <input id="permFileInput" type="file" class="hidden-input" accept="application/pdf" onchange="uploadFile('perm')" />
          <label class="upload-btn" for="tempFileInput">📄 Temporary Upload</label>
          <input id="tempFileInput" type="file" class="hidden-input" accept="application/pdf" onchange="uploadFile('temp')" />
        </div>
      </main>
      <div id="figureModal" class="modal">
        <div class="modal-content">
          <button class="close-btn" onclick="closeModal()">Close ✕</button>
          <img id="modalImage" src="" alt="Figure">
          <div class="modal-body">
            <h3 id="modalCaption"></h3>
            <p id="modalContext"></p>
          </div>
        </div>
      </div>
      <script>
        // Theme toggle
        const html=document.documentElement;
        const toggleBtn=document.getElementById('themeToggle');
        const saved=localStorage.getItem('theme');
        if(saved==='dark'){html.setAttribute('data-theme','dark');toggleBtn.textContent='☀';}
        toggleBtn.onclick=()=>{
          if(html.getAttribute('data-theme')==='dark'){
            html.removeAttribute('data-theme');toggleBtn.textContent='🌙';localStorage.setItem('theme','light');
          } else {
            html.setAttribute('data-theme','dark');toggleBtn.textContent='☀';localStorage.setItem('theme','dark');
          }
        };
        let sessionId=null;
        async function sendMessage(){
          const q=document.getElementById("question").value.trim();
          const imgLimit=parseInt(document.getElementById("imgLimit").value)||8;
          if(!q)return;
          const chatBox=document.getElementById("chat");
          const loading=document.getElementById("loading");
          chatBox.innerHTML+=`<div class='user-msg'><strong>You:</strong> ${q}</div>`;
          document.getElementById("question").value="";
          chatBox.scrollTop=chatBox.scrollHeight;
          loading.style.display="flex";
          try{
            const res=await fetch("/query",{method:"POST",headers:{"Content-Type":"application/json"},
              body:JSON.stringify({question:q,image_limit:imgLimit,session_id:sessionId})});
            const data=await res.json();
            if(!sessionId&&data.session_id)sessionId=data.session_id;
            let msgHtml="";
            if(data.error){
              msgHtml=`<div class='assistant-msg' style='color:#f87171;'>Error: ${data.error}</div>`;
            } else {
              msgHtml=`<div class='assistant-msg'><strong>Assistant:</strong> ${data.answer||"(no response)"}`;
              if(data.pdfs&&data.pdfs.length>0){
                msgHtml+="<div style='margin-top:0.6rem;'><h4>PDFs:</h4>";
                for(const pdf of data.pdfs){
                  const name=pdf.display_name||"View PDF";
                  msgHtml+=`<a href='${pdf.url}' target='_blank'
                    style='display:inline-block;margin:0.3rem;padding:0.4rem 0.7rem;
                    background:var(--accent);color:white;border-radius:6px;
                    font-size:0.9rem;text-decoration:none;'>📄 ${name}</a>`;
                }
                msgHtml+="</div>";
              }
              const imgs=(data.documents||[]).filter(d=>d.mime_type&&d.mime_type.startsWith("image/"));
              if(imgs.length>0){
                msgHtml+="<div><h4 style='margin-top:0.8rem;'>Figures:</h4><div class='figure-grid'>";
                for(const img of imgs){
                  const caption=(img.caption||"No caption").replace(/[`]/g,"'");
                  const context=(img.context||"No context available").replace(/[`]/g,"'").replace(/\n+/g," ");
                  msgHtml+=`
                    <div class='figure-card' onclick='openModal("${img.url}", \`${caption}\`, \`${context}\`)'>
                      <img src='${img.url}' alt='Figure'>
                      <div class='figure-overlay'>Click to view details</div>
                    </div>`;
                }
                msgHtml+="</div></div>";
              }
              msgHtml+="</div>";
            }
            chatBox.innerHTML+=msgHtml;
            chatBox.scrollTop=chatBox.scrollHeight;
          } catch(err){
            chatBox.innerHTML+=`<div class='assistant-msg' style='color:#f87171;'>Network error: ${err}</div>`;
          } finally{loading.style.display="none";}
        }
        async function uploadFile(type){
          const fileInput=document.getElementById(type==='perm'?'permFileInput':'tempFileInput');
          const file=fileInput.files[0]; if(!file)return;
          const uploading=document.getElementById("uploading"); uploading.style.display="flex";
          const form=new FormData(); form.append("file",file);
          if(type==="temp")form.append("session_id",sessionId||"");
          const endpoint=type==="perm"?"/upload_permanent":"/upload_temporary";
          try{await fetch(endpoint,{method:"POST",body:form});
            alert(`${type==='perm'?'Permanent':'Temporary'} upload successful`);
          }catch(err){alert("Upload failed: "+err);}finally{uploading.style.display="none";}
        }
        function openModal(url,cap,ctx){
          document.getElementById("modalImage").src=url;
          document.getElementById("modalCaption").innerText=cap;
          document.getElementById("modalContext").innerText=ctx;
          document.getElementById("figureModal").style.display="flex";
        }
        function closeModal(){document.getElementById("figureModal").style.display="none";}
        window.onclick=e=>{if(e.target===document.getElementById("figureModal"))closeModal();}
      </script>
    </body>
    </html>
    """

# -----------------------
# Query Endpoint (unchanged)
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

                structured_key = f"{base_dir}/structured.json"
                structured_data = None
                try:
                    obj = s3.get_object(Bucket=bucket, Key=structured_key)
                    structured_data = json.loads(obj["Body"].read())
                except Exception:
                    structured_data = None

                text_map = {}
                if structured_data and "texts" in structured_data:
                    text_map = {t["self_ref"]: t for t in structured_data["texts"]}

                for folder in ["images/", "output/"]:
                    for k in try_list(f"{base_dir}/{folder}"):
                        if k.lower().endswith((".png", ".jpg", ".jpeg", ".svg")):
                            if image_count >= image_limit:
                                break
                            img_info = make_presigned(bucket, k)
                            img_info["caption"] = "No caption found"
                            img_info["context"] = "No context available"

                            if structured_data and "pictures" in structured_data:
                                try:
                                    pic_entry = structured_data["pictures"][image_count]
                                    cap_ref = pic_entry["children"][0]["$ref"]
                                    caption_text = text_map.get(cap_ref, {}).get("text", "")
                                    img_info["caption"] = caption_text.strip() or img_info["display_name"]

                                    page_no = pic_entry["prov"][0].get("page_no", None)
                                    nearby_texts = [
                                        t["text"]
                                        for t in structured_data["texts"]
                                        if t["prov"][0].get("page_no") == page_no
                                    ]
                                    img_info["context"] = " ".join(nearby_texts[:3])
                                except Exception:
                                    pass

                            links.append(img_info)
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
# Upload Endpoints (unchanged)
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
        return {"status": "ok", "filename": file.filename, "chunk_count": len(extracted_text.split("\n\n"))}
    except Exception as e:
        return {"status": "error", "error": str(e)}

# -----------------------
# Run
# -----------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
