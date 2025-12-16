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


# Forward

ngrok --config /home/murtato/snap/ngrok/315/.config/ngrok/ngrok.yml http 8000

# Push to specific bucket

python s3_push.py output/ --bucket cosmology-paper-1

# Use PDF Chunker
cd pdf_chunker
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

    * THE PREAMBLES AFFECT THE RETURNED SOURCES AND SCORES?

    * Need to fix AmazonBedrockExecutionRoleForKnowledgeBase_c0q2t

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