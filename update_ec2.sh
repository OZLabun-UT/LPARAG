#!/bin/bash
set -e

ssh -i /home/murtato/phys/rag-llm/rag-instance-actual.pem ubuntu@3.142.186.60 <<'EOF'
cd ~/tau-topical-expert
git pull origin main
sudo systemctl restart tau
EOF
