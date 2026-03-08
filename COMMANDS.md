# LWFA RAG Agent with AnythingLLM

Instructions:

sudo bash run_space.sh
docker compose up -d

sudo docker start anythingllm
sudo docker logs -n 200 anythingllm

Stop start:
sudo docker compose up -d
sudo docker compose stop
Check:
sudo docker compose ps
Expose:
ngrok http 3001

# AWS Deploy

source .venv/bin/activate

python -m aws_interface.main
python3 -m aws_interface.main


cd pdf_chunker

# Full runs (300 papers, 5 buckets each)
python run_lwfa_sim_pipeline.py
python run_lwfa_not_sim_pipeline.py
python run_pwa_pipeline.py
python run_swa_pipeline.py

# Quick tests (10 papers, 2 buckets)
python run_lwfa_sim_pipeline.py --test
python run_lwfa_not_sim_pipeline.py --test
python run_pwa_pipeline.py --test
python run_swa_pipeline.py --test


# Export papers master list (S3 buckets with 'new' prefix → CSV)

source .venv/bin/activate
cd pdf_chunker
python export_papers_csv.py -o papers_master.csv
python export_papers_csv.py --output my_papers.csv --bucket-prefix new

# Pipelines use papers_master.csv for deduplication: before downloading from arXiv,
# they skip papers whose title (similarity ≥ 0.85) or arxiv_id matches existing entries.
# Run export_papers_csv first to generate/refresh the CSV, then run pipelines.
# Use --no-dedup to disable: python run_pwa_pipeline.py --no-dedup
#
# Pipelines also check S3 bucket capacity: before each batch, they skip buckets that
# already have ≥ papers_per_bucket (80) papers, moving to the next bucket.
#
# Rebalance buckets: move excess papers from over-full buckets to ones with space
cd pdf_chunker
python rebalance_buckets.py --dry-run   # preview
python rebalance_buckets.py            # run
python rebalance_buckets.py --max-papers 80 --bucket-prefix new
#
# Papers CSV statistics and plots
cd pdf_chunker
python papers_stats.py -c papers_master.csv -o stats_plots

# See bucket IDs

aws bedrock-agent list-knowledge-bases \
  --region us-east-2 \
  --output table

# Forward

ngrok --config /home/murtato/snap/ngrok/315/.config/ngrok/ngrok.yml http 8000

# Push to specific bucket (uses Docling/new_chunker; includes structured.json)

python s3_push.py pdfs/ --bucket swa-general-3 --chunk-s3-only

# Create new S3 bucket + chunk first 80 PDFs from pdfs2 + upload

cd pdf_chunker
python s3_push.py --create-bucket-and-upload my-new-bucket
PDF_LIMIT=50 python s3_push.py --create-bucket-and-upload my-bucket  # custom limit

# Full pipelines: arXiv → chunk → S3 (resume-safe)

# How it works:
# - PDFs are saved once to pdf_chunker/pipeline_pdfs/
# - If that folder already has PDFs, download is skipped (safe to re-run after a crash)
# - Papers are processed in batches of 8: chunk → upload to S3 → delete those 8 from the folder
# - When the folder is empty, the run is done

# --- PWA example (Plasma Wakefield, excluding laser wakefield) ---

# From repo root. Quick test (10 papers, 2 buckets):
cd pdf_chunker && python run_pwa_pipeline.py --test

# Full run (300 papers, 5 buckets):
cd pdf_chunker && python run_pwa_pipeline.py

# What the PWA pipeline does:
# - Query: "plasma wakefield acceleration" AND NOT "laser wakefield acceleration"
# - Buckets: new-pwa-1, new-pwa-2, ... (2 with --test, 5 full)
# - First run: downloads papers into pipeline_pdfs/, then processes in batches of 8
# - If you stop and run again: skips download, continues with remaining PDFs

# --- Other pipelines (same idea) ---

cd pdf_chunker

# LWFA + simulation
python run_lwfa_sim_pipeline.py --test
python run_lwfa_sim_pipeline.py

# LWFA NOT simulation
python run_lwfa_not_sim_pipeline.py --test
python run_lwfa_not_sim_pipeline.py

# Structure wakefield
python run_swa_pipeline.py --test
python run_swa_pipeline.py

# Custom PDF folder
PIPELINE_PDFS_DIR=/path/to/pdfs python run_pwa_pipeline.py --test

# Download arXiv papers to pdfs2 (with query and boolean filters)

cd pdf_chunker
python arxiv_download.py "laser wakefield acceleration" --limit 10
python arxiv_download.py -q "plasma accelerator" -c physics.plasm-ph -l 5 -o pdfs2
python arxiv_download.py --query-file query.json --output pdfs2

# Use PDF Chunker
python new_chunker.py ./pdfs

python s3_push.py

"What is One of the major goals of the ultrahigh-gradient,
plasma-based acceleration scheme"

# EC2 Deploy

PERSONAL

SSH in:
ssh -i rag-instance.pem ubuntu@18.224.212.81

Setup:
sudo apt update && sudo apt upgrade -y

sudo apt update && sudo apt upgrade -y
sudo apt install git python3-pip python3-venv nginx -y

Run:
 uvicorn app:app --host 0.0.0.0 --port 8000

ACTUAL:

chmod 400 rag-instance-actual.pem

ssh -i rag-instance-actual.pem ubuntu@3.142.186.60

scp -i  rag-instance-actual.pem .env ubuntu@3.142.186.60:/home/ubuntu/tau-topical-expert/.env

# Update EC2
ssh -i rag-instance-actual.pem ubuntu@3.142.186.60
cd ~/tau-topical-expert
git pull origin main
sudo systemctl restart tau

OR: just use the update_ec2.sh executable

# Github

See origins:
git remote -v

# Test Questions

"What is the maximum electron energy as a function of laser propagation distance?"

"Explain LWFA to me."

# Working Models

Claude Opus 4 (slow)
Claude Sonnet 4
Claude 3.5 Sonnet v2
Nova Pro
Nova Premier
Nova Lite
Nova Micro
Llama 4 Scout 17B
Llama 3.2 90B
DeepSeek R1



# Bad Models

Claude Opus 4.1


# To-dos

https://github.com/Mahdisadjadi/arxivscraper

    Figure out how to make it so ppl can make accounts and stuff
    Can we figure out how to see relevant appendices when there's not much nat lang there?

    * Get name that we want to use as domain name
    * Make tabulated data out of images im2graph
    * Add help features and tutorials
    * Relevance threshold
    * Wikipedia scraper and openstax?
    * APS Physiscs Magazine
    * Exclude top relevance for new info?
    * Make a comprehensive system prompt
    * Add back button to pdf upload
    * Save all logs
    * Most relevant 20 papers not just 2
    * Make an llm that operates on Ou's subset
    * Title of file as article title
    * Master json to check duplicates
    * Can we structure router prompt with clear heirarchical logic?
    * For now: do pic and learn physics, later learn GNN, connect to llm eventually
      * Notes on pic code from someone and one is lwfa
      * High power laser matter interactions
      * Save all questions that you have about it

    * THE PREAMBLES AFFECT THE RETURNED SOURCES AND SCORES?
    * Need to fix AmazonBedrockExecutionRoleForKnowledgeBase_c0q2t

    * Do real smilei case
    * Finish populating buckets
    * Test set make it easy for ou and lance to populate
      * maybe multiple
    * Keep reading textbook

    Done:
    * Save conversations to the side for people to flip back and forth
    * "Use your general knowledge"
    * Fix metadata being wrapped in caption
    * Convos saved in local memory
    * Switch models
    * Mark junk
    * Stop multiple of same paper from being in database
    * Fix button UI
    * Arxiv scraper
    * Fix ui hide button
    * Returned images should be indexed and kept in the llm memory (maybe make them selectable)
    * Failed uploads 
    * Make it show returned relevance scores
    * Fix knowledge base resync to reload data sources
    * Fix paper upload to register new buckets as data sources

sudo tee /etc/systemd/system/tau.service > /dev/null <<'EOF'
[Unit]
Description=Tau FastAPI Service
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/tau-topical-expert
EnvironmentFile=/home/ubuntu/tau-topical-expert/.env
ExecStart=/home/ubuntu/tau-topical-expert/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemd-analyze verify /etc/systemd/system/tau.service
sudo systemctl daemon-reload
sudo systemctl start tau
systemctl status tau.service