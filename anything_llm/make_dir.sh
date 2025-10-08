mkdir -p $HOME/anythingllm_storage
sudo chown -R 1000:1000 $HOME/anythingllm_storage

sudo docker run -d --name anythingllm \
  -p 3001:3001 \
  -e PORT=3001 \
  -e STORAGE_DIR=/app/server/storage \
  -v $HOME/anythingllm_storage:/app/server/storage \
  mintplexlabs/anythingllm:latest
