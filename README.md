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
2
source .venv/bin/activate
cd aws-interface
uvicorn main:app --reload --port 8000


# Use PDF Chunker

python chunker.py ./pdfs

python s3_push.py

"What is The radiation energy as a function of distance z through the undulator for different values of energy spread?"


# To-dos

    Benchmark w confusing context
    Benchmark long term memory

    Make links points to pdf
    Figure out how to make it so ppl can make accounts and stuff
    user facing chat history stuff
    Do it w the advanced pdf chunker
    Make some access method using grok tunnel?
    Make website to host arxiv scraper and also pdf uploads from tau ppl
    Make list of aws enabled stuff like web search for llm
    Can we figure out how to see relevant appendices when there's not much nat lang there?
    AWS enabled interfact

    General steps:

