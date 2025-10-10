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

# Forward

ngrok --config /home/murtato/snap/ngrok/315/.config/ngrok/ngrok.yml http 8000



# Use PDF Chunker

python chunker.py ./pdfs

python s3_push.py

"What is One of the major goals of the ultrahigh-gradient,
plasma-based acceleration scheme"

# EC2 Deploy

>SSH into EC2
ssh -i "your-key.pem" ec2-user@<your-ec2-public-ip>

>Install core dependencies
sudo yum update -y
sudo yum install git python3 python3-pip nginx -y

>Clone your project
git clone https://github.com/<you>/RAG-LLM.git
cd RAG-LLM

>Set up virtual environment
python3 -m venv .venv
source .venv/bin/activate

>Install dependencies
pip install --upgrade pip
pip install fastapi uvicorn boto3 python-dotenv transformers tqdm docling docling-core



# To-dos

    Pressing: Get EC2 permissions
    Add captions to images and some intro context

    Benchmark w confusing context
    Benchmark long term memory

    Figure out how to make it so ppl can make accounts and stuff
    Can we figure out how to see relevant appendices when there's not much nat lang there?
    AWS enabled interfact

    General steps:

