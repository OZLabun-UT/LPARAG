# LWFA RAG Agent with AnythingLLM

Instructions:


sudo bash run_space.sh
docker compose up -d

#

sudo docker start anythingllm
sudo docker logs -n 200 anythingllm

Stop start:
sudo docker compose up -d
sudo docker compose stop
Check:
sudo docker compose ps
Expose:
ngrok http 3001
