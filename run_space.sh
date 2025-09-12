docker pull mintplexlabs/anythingllm:latest
docker stop anythingllm 2>/dev/null || true
docker rm anythingllm 2>/dev/null || true
export ALLM_STORAGE="$HOME/anythingllm_storage"
mkdir -p "$ALLM_STORAGE"
sudo chown -R 1000:1000 "$ALLM_STORAGE"
sudo chmod -R u+rwX,g+rwX "$ALLM_STORAGE"
sudo docker run -d --name anythingllm \
  -p 3001:3001 \
  -e PORT=3001 \
  -e STORAGE_DIR=/app/server/storage \
  -v "$ALLM_STORAGE":/app/server/storage \
  mintplexlabs/anythingllm:latest