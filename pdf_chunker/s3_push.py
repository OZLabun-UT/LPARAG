import boto3
import os
import sys
import shutil
from dotenv import load_dotenv
import subprocess
from pathlib import Path
import time

load_dotenv(override=True)

AWS_REGION = "us-east-2"


def sync_to_s3(local_folder, bucket_name, prefix="kb-data"):
    """
    Uploads local_folder to S3, then deletes local files if upload succeeds.
    """
    local_path = Path(local_folder)
    if not local_path.exists():
        print(f"[!] Folder not found: {local_path}")
        sys.exit(2)

    print(f"[•] Uploading {local_folder} → s3://{bucket_name}/{prefix}/ ...")
    subprocess.run([
        "aws", "s3", "sync", str(local_folder),
        f"s3://{bucket_name}/{prefix}/"
    ], check=True)
    print(f"[✓] Synced {local_folder} → s3://{bucket_name}/{prefix}/")

    # Cleanup
    try:
        for child in local_path.iterdir():
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
        print(f"[🗑] Cleaned up local files in {local_folder}")
    except Exception as e:
        print(f"[!] Cleanup failed: {e}")


def resync_knowledge_base(kb_id: str, region: str = AWS_REGION):
    """
    Triggers a Bedrock Knowledge Base ingestion sync using KB_ID and DATA_SOURCE_ID.
    """
    client = boto3.client("bedrock-agent", region_name=region)

    data_source_id = os.getenv("DATA_SOURCE_ID")
    if not data_source_id:
        print("[!] Missing DATA_SOURCE_ID in environment (.env)")
        sys.exit(2)

    print(f"[•] Starting knowledge base sync for {kb_id} (data source {data_source_id}) ...")

    try:
        # Start the ingestion job
        resp = client.start_ingestion_job(
            knowledgeBaseId=kb_id,
            dataSourceId=data_source_id
        )
        job_id = resp["ingestionJob"]["ingestionJobId"]
        print(f"[→] Ingestion job started: {job_id}")

        # Optional: poll for completion
        while True:
            job = client.get_ingestion_job(
                knowledgeBaseId=kb_id,
                dataSourceId=data_source_id,
                ingestionJobId=job_id
            )
            status = job["ingestionJob"]["status"]
            if status in ("COMPLETED", "COMPLETE", "FAILED"):
                print(f"[✓] Ingestion job finished with status: {status}")
                break
            print(f"   ⏳ In progress... ({status})")
            time.sleep(5)

    except Exception as e:
        print(f"[!] Failed to trigger knowledge base sync: {e}")
        sys.exit(2)



if __name__ == "__main__":
    # Usage:
    #   python s3_push.py [output_dir]
    #   python s3_push.py --resync-only
    args = sys.argv[1:]
    bucket_name = os.getenv("PDF_BUCKET")
    kb_id = os.getenv("KB_ID")

    if not args:
        print("[!] Missing arguments: specify output folder or --resync-only")
        sys.exit(2)

    if "--resync-only" in args:
        if not kb_id:
            print("[!] KB_ID missing from environment")
            sys.exit(2)
        resync_knowledge_base(kb_id)
        sys.exit(0)

    local_output = Path(args[0])

    if not bucket_name:
        print("[!] Missing PDF_BUCKET in environment")
        sys.exit(2)

    sync_to_s3(local_output, bucket_name)

    if kb_id:
        resync_knowledge_base(kb_id)
