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
cd aws-interface
uvicorn main:app --reload --port 8000


# Use PDF Chunker

python chunker.py ./pdfs

"What is The radiation energy as a function of distance z through the undulator for different values of energy spread?"

