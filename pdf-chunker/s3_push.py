import boto3
import os
import sys
from dotenv import load_dotenv
import subprocess

load_dotenv(override=True)


AWS_REGION="us-east-2"

def sync_to_s3(local_folder, bucket_name, prefix="kb-data"):
    # Uses AWS CLI sync (faster for many files)
    subprocess.run([
        "aws", "s3", "sync", local_folder,
        f"s3://{bucket_name}/{prefix}/"
    ], check=True)
    print(f"[✓] Synced {local_folder} → s3://{bucket_name}/{prefix}/")


if __name__ == "__main__":
    local_output = sys.argv[1] if len(sys.argv) > 1 else "output"
    bucket_name = os.getenv("PDF_BUCKET")  # configurable
    
    sync_to_s3(local_output, bucket_name)
