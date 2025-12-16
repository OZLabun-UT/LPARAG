import boto3
import os
import sys
import shutil
from dotenv import load_dotenv
import subprocess
from pathlib import Path
import time
import hashlib
import re

load_dotenv(override=True)

AWS_REGION = "us-east-2"


def parse_bucket_arg(args: list[str]) -> tuple[list[str], str | None]:
    """Extract --bucket BUCKET_NAME from args."""
    if "--bucket" in args:
        idx = args.index("--bucket")
        try:
            bucket = args[idx + 1]
        except IndexError:
            print("[!] --bucket requires a value")
            sys.exit(2)

        # remove flag + value from args
        new_args = args[:idx] + args[idx + 2 :]
        return new_args, bucket

    return args, None


def get_md5(file_path: Path) -> str:
    """Return MD5 hash of a file."""
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def s3_object_md5(bucket: str, key: str) -> str | None:
    """Return ETag (MD5) of an object in S3, or None if not found."""
    s3 = boto3.client("s3", region_name=AWS_REGION)
    try:
        obj = s3.head_object(Bucket=bucket, Key=key)
        return obj["ETag"].strip('"')
    except s3.exceptions.ClientError:
        return None


def folder_exists_in_s3(bucket_name: str, prefix: str) -> bool:
    """Return True if a folder/prefix already exists in the given S3 bucket."""
    s3 = boto3.client("s3", region_name=AWS_REGION)
    try:
        resp = s3.list_objects_v2(Bucket=bucket_name, Prefix=prefix, MaxKeys=1)
        return "Contents" in resp and len(resp["Contents"]) > 0
    except Exception as e:
        print(f"[!] Failed to check S3 for prefix {prefix}: {e}")
        return False


def duplicate_pdfs_exist(bucket_name: str, local_folder: Path, s3_prefix: str) -> bool:
    """Check if PDFs in local_folder already exist in S3 with identical content."""
    s3 = boto3.client("s3", region_name=AWS_REGION)
    try:
        resp = s3.list_objects_v2(Bucket=bucket_name, Prefix=s3_prefix)
        existing = {Path(obj["Key"]).name: obj["ETag"].strip('"') for obj in resp.get("Contents", []) if obj["Key"].lower().endswith(".pdf")}
    except Exception as e:
        print(f"[!] Could not list objects in S3: {e}")
        existing = {}

    duplicates = []
    for pdf in local_folder.glob("*.pdf"):
        local_md5 = get_md5(pdf)
        s3_md5 = existing.get(pdf.name)
        if s3_md5 == local_md5:
            duplicates.append(pdf.name)

    if duplicates:
        print(f"[⚠] Identical PDFs already exist in S3: {', '.join(duplicates)}")
        choice = input("   Overwrite them? [y/N]: ").strip().lower()
        if choice != "y":
            print("[⏭] Skipping upload.")
            return True
    return False


def sync_to_s3(local_folder, bucket_name, prefix="kb-data"):
    """
    Uploads local_folder to S3, skipping if identical PDFs already exist.
    Deletes local files if upload succeeds.
    """
    local_path = Path(local_folder)
    if not local_path.exists():
        print(f"[!] Folder not found: {local_path}")
        sys.exit(2)

    folder_name = local_path.name
    s3_prefix = f"{prefix}/{folder_name}/"

    # --- Check if folder or PDFs already exist ---
    if folder_exists_in_s3(bucket_name, s3_prefix):
        if duplicate_pdfs_exist(bucket_name, local_path, s3_prefix):
            return  # skip upload if duplicates found and user chose not to overwrite

    print(f"[•] Uploading {local_folder} → s3://{bucket_name}/{s3_prefix} ...")
    subprocess.run([
    "aws", "s3", "sync", str(local_folder),
    f"s3://{bucket_name}/{s3_prefix}/",
    "--exact-timestamps"
    ], check=True)

    print(f"[✓] Synced {local_folder} → s3://{bucket_name}/{s3_prefix}")

    # --- Cleanup local folder ---
    try:
        shutil.rmtree(local_path)
        print(f"[🗑] Deleted local folder after successful upload: {local_folder}")
    except Exception as e:
        print(f"[!] Cleanup failed: {e}")



def resync_knowledge_base(kb_id: str, region: str = AWS_REGION):
    """Auto-detect new tau-papers-* buckets and ensure each is a data source."""
    client = boto3.client("bedrock-agent", region_name=region)
    s3 = boto3.client("s3", region_name=region)
    base_name = os.getenv("PDF_BUCKET_BASE", "tau-papers")

    # List all relevant S3 buckets
    all_buckets = [b["Name"] for b in s3.list_buckets()["Buckets"]]
    paper_buckets = [b for b in all_buckets if b.startswith(base_name)]
    print(f"[🪣] Found paper buckets: {paper_buckets}")

    # Get existing data sources for the KB
    existing_ds = client.list_data_sources(knowledgeBaseId=kb_id)["dataSourceSummaries"]
    existing_names = {d["name"] for d in existing_ds}
    print(f"[📚] Existing data sources: {existing_names}")

    # Create data sources for any missing buckets
    for bucket in paper_buckets:
        if bucket not in existing_names:
            print(f"[➕] Creating new data source for bucket: {bucket}")
            client.create_data_source(
                knowledgeBaseId=kb_id,
                name=bucket,
                dataSourceConfiguration={
                    "type": "S3",
                    "s3Configuration": {
                        "bucketArn": f"arn:aws:s3:::{bucket}"
                    }
                },
                description="Auto-added bucket",
            )


    # Trigger ingestion for all data sources
    for ds in client.list_data_sources(knowledgeBaseId=kb_id)["dataSourceSummaries"]:
        ds_id = ds["dataSourceId"]
        print(f"[⚙️] Starting ingestion for {ds['name']} ...")
        resp = client.start_ingestion_job(
            knowledgeBaseId=kb_id,
            dataSourceId=ds_id
        )
        job_id = resp["ingestionJob"]["ingestionJobId"]
        print(f"   ↳ job_id = {job_id}")


if __name__ == "__main__":
    args = sys.argv[1:]

    # parse --bucket
    args, cli_bucket = parse_bucket_arg(args)

    bucket_name = cli_bucket or os.getenv("PDF_BUCKET")
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

    if not bucket_name:
        print("[!] Missing bucket. Use --bucket or set PDF_BUCKET")
        sys.exit(2)

    local_output = Path(args[0])

    sync_to_s3(local_output, bucket_name)

    if kb_id:
        resync_knowledge_base(kb_id)
