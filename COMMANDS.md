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

cd ~/tau-topical-expert
git pull origin main
sudo systemctl restart tau

# Github

See origins:
git remote -v

# Test Questions

"What is the maximum electron energy as a function of laser propagation distance?"

"Explain LWFA to me."


# To-dos

    Pressing: Get EC2 permissions
    Add captions to images and some intro context

    Benchmark w confusing context
    Benchmark long term memory

    Figure out how to make it so ppl can make accounts and stuff
    Can we figure out how to see relevant appendices when there's not much nat lang there?
    * Stop multiple of same paper from being in database
    * Get name that we want to use as domain name
    * Returned images should be indexed and kept in the llm memory (maybe make them selectable)
    * Keep a version with smaller controlled knowledge base
    * Make toggle for which AI to use 
    * Make tabulated data out of images im2graph
    * Make it show returned relevance scores
    * Fix ui hide button
    * Fix metadata being wrapped in caption
    * Mark junk?
    * Fix button UI
    * Everytime low relevance score, prompt user to reformulate and lower threshold
    * Add help features and tutorials

    Done:
    * Save conversations to the side for people to flip back and forth
    * Convos saved in local memory


    
[Unit]
Description=Tau FastAPI Service
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/tau-topical-expert
EnvironmentFile=/home/ubuntu/tau-topical-expert/.env
ExecStart=/home/ubuntu/tau-topical-expert/.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target

