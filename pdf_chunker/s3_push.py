import boto3
import os
import sys
import shutil
import tempfile
from dotenv import load_dotenv
from botocore.exceptions import ClientError
import subprocess
from pathlib import Path
import time
import hashlib
import re

load_dotenv(override=True)

AWS_REGION = os.getenv("AWS_REGION", "us-east-2")


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


def count_papers_in_bucket(
    bucket_name: str,
    prefix: str = "kb-data",
    region: str | None = None,
) -> int:
    """Count paper folders (papers) already in the bucket under prefix/output/."""
    s3 = boto3.client("s3", region_name=region or AWS_REGION)
    count = 0
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(
            Bucket=bucket_name,
            Prefix=f"{prefix}/output/",
            Delimiter="/",
        ):
            count += len(page.get("CommonPrefixes", []))
    except Exception as e:
        print(f"[!] Could not count papers in {bucket_name}: {e}")
    return count


def folder_exists_in_s3(bucket_name: str, prefix: str) -> bool:
    """Return True if a folder/prefix already exists in the given S3 bucket."""
    s3 = boto3.client("s3", region_name=AWS_REGION)
    try:
        resp = s3.list_objects_v2(Bucket=bucket_name, Prefix=prefix, MaxKeys=1)
        return "Contents" in resp and len(resp["Contents"]) > 0
    except Exception as e:
        print(f"[!] Failed to check S3 for prefix {prefix}: {e}")
        return False


def ensure_bucket_exists(bucket_name: str, region: str | None = None) -> None:
    """Create S3 bucket if it does not exist. Idempotent."""
    region = region or AWS_REGION
    bucket_name = bucket_name.lower().strip()
    try:
        s3 = boto3.client("s3", region_name=region)
        if region == "us-east-1":
            s3.create_bucket(Bucket=bucket_name)
        else:
            s3.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={"LocationConstraint": region},
            )
        print(f"[🪣] Created bucket: {bucket_name}")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("BucketAlreadyExists", "BucketAlreadyOwnedByYou"):
            pass  # already exists
        elif code in ("InvalidAccessKeyId", "SignatureDoesNotMatch", "AccessDenied"):
            print(f"[❌] AWS authentication failed for bucket {bucket_name}")
            print(f"     Error: {e}")
            print(f"     → Check your AWS credentials in .env:")
            print(f"        AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be valid")
            raise
        else:
            raise
    except Exception as e:
        if "InvalidAccessKeyId" in str(e) or "NoCredentialsError" in str(type(e).__name__):
            print(f"[❌] AWS credentials not found or invalid")
            print(f"     → Ensure .env contains valid AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY")
        raise


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

    s3_uri = f"s3://{bucket_name}/{s3_prefix.rstrip('/')}/"
    print(f"[•] Uploading {local_folder} → {s3_uri}")
    subprocess.run([
        "aws", "s3", "sync", str(local_folder),
        s3_uri,
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

def upload_only_to_s3(local_folder: Path, bucket_name: str, prefix="kb-data"):
    """
    Upload local_folder to the specified S3 bucket ONLY.
    No Bedrock calls. No auto-discovery. No ingestion.
    """

    local_path = Path(local_folder)
    if not local_path.exists():
        print(f"[!] Folder not found: {local_path}")
        sys.exit(2)

    folder_name = local_path.name
    s3_prefix = f"{prefix}/{folder_name}/"

    # --- Check for duplicates ---
    if folder_exists_in_s3(bucket_name, s3_prefix):
        if duplicate_pdfs_exist(bucket_name, local_path, s3_prefix):
            print("[⏭] Upload skipped due to duplicates.")
            return

    print(f"[•] Uploading {local_folder} → s3://{bucket_name}/{s3_prefix} ...")

    s3_uri = f"s3://{bucket_name}/{s3_prefix.rstrip('/')}/"
    subprocess.run(
        [
            "aws", "s3", "sync",
            str(local_path),
            s3_uri,
            "--exact-timestamps"
        ],
        check=True
    )

    print(f"[✓] Upload complete: s3://{bucket_name}/{s3_prefix}")

    # --- Optional cleanup ---
    try:
        shutil.rmtree(local_path)
        print(f"[🗑] Deleted local folder after upload: {local_folder}")
    except Exception as e:
        print(f"[!] Cleanup failed: {e}")

def chunk_and_upload_s3_only(
    pdf_input_dir: Path,
    bucket_name: str,
    prefix: str = "kb-data"
):
    """
    Runs the PDF chunking pipeline, then uploads the resulting output
    to the specified S3 bucket ONLY. No Bedrock interaction.
    """

    pdf_input_dir = Path(pdf_input_dir)
    if not pdf_input_dir.exists():
        print(f"[!] PDF input dir not found: {pdf_input_dir}")
        sys.exit(2)

    print(f"[⚙️] Running Docling chunker (new_chunker) on {pdf_input_dir} ...")

    script_dir = Path(__file__).resolve().parent
    output_dir = pdf_input_dir.parent / "output"
    subprocess.run(
        [sys.executable, str(script_dir / "new_chunker.py"), str(pdf_input_dir), str(output_dir)],
        cwd=script_dir,  # ensure new_chunker imports work
        check=True
    )

    if not output_dir.exists():
        print("[❌] Chunker did not produce output/")
        sys.exit(2)

    upload_only_to_s3(
        local_folder=output_dir,
        bucket_name=bucket_name,
        prefix=prefix
    )

    print("[✅] Chunk + upload complete (no Bedrock).")


def create_bucket_and_upload_from_pdfs2(
    bucket_name: str,
    pdf_limit: int = 80,
    pdf_offset: int = 0,
    pdfs2_dir: Path | None = None,
    region: str | None = None,
    prefix: str = "kb-data",
    pdf_batch_size: int = 8,
) -> None:
    """
    Create a new S3 bucket, take PDFs from pdfs2 [offset:offset+limit], chunk with Docling,
    and upload to the bucket. Processes in batches to reduce memory use (avoids OOM/SIGKILL).

    Args:
        bucket_name: Name for the new S3 bucket (must be globally unique, lowercase, 3-63 chars).
        pdf_limit: Max number of PDFs to process (default: 80).
        pdf_offset: Start index into sorted PDF list (default: 0).
        pdfs2_dir: Path to pdfs2 folder (default: pdf_chunker/pdfs2).
        region: AWS region for bucket (default: AWS_REGION).
        prefix: S3 prefix for uploaded data (default: kb-data).
        pdf_batch_size: PDFs per batch to avoid OOM/timeout (default: 8).
    """
    region = region or AWS_REGION
    s3 = boto3.client("s3", region_name=region)

    # Create bucket
    bucket_name = bucket_name.lower().strip()
    try:
        if region == "us-east-1":
            s3.create_bucket(Bucket=bucket_name)
        else:
            s3.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={"LocationConstraint": region},
            )
        print(f"[🪣] Created bucket: {bucket_name}")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("BucketAlreadyExists", "BucketAlreadyOwnedByYou"):
            print(f"[ℹ️] Bucket {bucket_name} already exists, continuing")
        else:
            raise

    # Resolve pdfs2 path
    script_dir = Path(__file__).resolve().parent
    pdfs2 = Path(pdfs2_dir) if pdfs2_dir else script_dir / "pdfs2"
    if not pdfs2.exists():
        raise FileNotFoundError(f"pdfs2 directory not found: {pdfs2}")

    all_pdfs = sorted(pdfs2.glob("*.pdf"))
    pdfs = all_pdfs[pdf_offset : pdf_offset + pdf_limit]
    if not pdfs:
        raise FileNotFoundError(
            f"No PDFs in range [{pdf_offset}:{pdf_offset + pdf_limit}] (found {len(all_pdfs)} total in {pdfs2})"
        )

    print(f"[📂] Using PDFs {pdf_offset + 1}-{pdf_offset + len(pdfs)} of {len(all_pdfs)} from {pdfs2}")
    if len(pdfs) > pdf_batch_size:
        print(f"[📦] Processing in batches of {pdf_batch_size} to reduce memory use")

    # Process in batches to avoid OOM
    temp_root = Path(tempfile.mkdtemp(prefix="pdfs2_upload_"))
    try:
        for batch_start in range(0, len(pdfs), pdf_batch_size):
            batch = pdfs[batch_start : batch_start + pdf_batch_size]
            batch_num = batch_start // pdf_batch_size + 1
            total_batches = (len(pdfs) + pdf_batch_size - 1) // pdf_batch_size
            print(f"\n[📦] Batch {batch_num}/{total_batches} ({len(batch)} PDFs)")
            temp_dir = temp_root / f"batch_{batch_num}"
            temp_dir.mkdir()
            for pdf in batch:
                shutil.copy2(pdf, temp_dir / pdf.name)
            chunk_and_upload_s3_only(temp_dir, bucket_name, prefix=prefix)
            shutil.rmtree(temp_dir, ignore_errors=True)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
        output_dir = temp_root.parent / "output"
        if output_dir.exists():
            shutil.rmtree(output_dir, ignore_errors=True)

    print(f"[✅] Done: {len(pdfs)} papers chunked and uploaded to s3://{bucket_name}/{prefix}/")


if __name__ == "__main__":
    args = sys.argv[1:]
    args, cli_bucket = parse_bucket_arg(args)

    bucket_name = cli_bucket or os.getenv("PDF_BUCKET")

    if not args:
        print("[!] Missing arguments")
        sys.exit(2)

    if "--create-bucket-and-upload" in args:
        args.remove("--create-bucket-and-upload")
        bucket_name = (args[0] if args else None) or bucket_name
        if not bucket_name:
            print("[!] Bucket name required. Use: python s3_push.py --create-bucket-and-upload BUCKET_NAME")
            sys.exit(2)
        pdf_limit = int(os.getenv("PDF_LIMIT", "80"))
        pdf_offset = int(os.getenv("PDF_OFFSET", "0"))
        pdf_batch_size = int(os.getenv("PDF_BATCH_SIZE", "8"))
        pdfs2_dir = Path(args[1]) if len(args) > 1 else None
        try:
            create_bucket_and_upload_from_pdfs2(
                bucket_name=bucket_name,
                pdf_limit=pdf_limit,
                pdf_offset=pdf_offset,
                pdfs2_dir=pdfs2_dir,
                prefix="kb-data",
                pdf_batch_size=pdf_batch_size,
            )
        except Exception as e:
            print(f"[❌] {e}")
            sys.exit(1)
        sys.exit(0)

    if "--chunk-s3-only" in args:
        args.remove("--chunk-s3-only")

        if not bucket_name:
            print("[!] Missing bucket. Use --bucket or set PDF_BUCKET")
            sys.exit(2)

        pdf_dir = Path(args[0])   # usually pdfs/
        chunk_and_upload_s3_only(pdf_dir, bucket_name)
        sys.exit(0)

    kb_id = os.getenv("KB_ID")

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
