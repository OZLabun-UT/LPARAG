"""
Generic pipeline: arXiv download (once) → chunk in batches of 8 → upload to S3 → delete PDFs after each batch.

- Uses a persistent PDF folder. If it already has PDFs, scraping is skipped (resume-safe).
- Processes in batches of 8; after each batch is uploaded, those PDFs are deleted from the folder.
- Robust to failure: restart continues from remaining PDFs without re-downloading.
"""
import os
import shutil
import tempfile
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables BEFORE importing boto3-dependent modules
load_dotenv(override=True)

from pdf_chunker.arxiv_download import fetch_and_download
from pdf_chunker.export_papers_csv import load_existing_papers_from_csv
from pdf_chunker.s3_push import chunk_and_upload_s3_only, ensure_bucket_exists, count_papers_in_bucket


def run_pipeline(
    query_payload: dict,
    bucket_prefix: str,
    total_papers: int = 300,
    papers_per_bucket: int = 80,
    num_buckets: int = 5,
    prefix: str = "kb-data",
    verify: bool = True,
    pdf_batch_size: int = 8,
    pdf_dir: Path | None = None,
    *,
    papers_csv_path: Path | str | None = None,
    dedup: bool = True,
    similarity_threshold: float = 0.85,
) -> int:
    """
    Run pipeline: download papers once (only if PDF folder is empty) → process in batches of 8 → upload → delete each batch.

    Args:
        query_payload: arXiv query dict (and, not, groups, limit).
        bucket_prefix: Base name for buckets, e.g. "new-lwfa-sim" → new-lwfa-sim-1, -2, ...
        total_papers: Max papers to download when folder is empty.
        papers_per_bucket: Papers per bucket (80).
        num_buckets: Number of buckets (5).
        prefix: S3 prefix for uploaded data (kb-data).
        verify: Whether to verify chunk structure in S3 after upload.
        pdf_batch_size: PDFs per batch (default: 8); each batch is chunked, uploaded, then deleted.
        pdf_dir: Persistent directory for PDFs (default: pdf_chunker/pipeline_pdfs). Set via PIPELINE_PDFS_DIR to override.
        papers_csv_path: Path to papers_master.csv for deduplication. Default: pdf_chunker/papers_master.csv or PAPERS_MASTER_CSV env.
        dedup: If True, skip papers whose title/arxiv_id matches existing entries in the CSV.
        similarity_threshold: Title similarity threshold for dedup (0-1, default 0.85).

    Returns:
        0 on success, 1 on failure.
    """
    script_dir = Path(__file__).resolve().parent
    persistent_dir = Path(pdf_dir) if pdf_dir else Path(os.getenv("PIPELINE_PDFS_DIR", str(script_dir / "pipeline_pdfs")))
    persistent_dir.mkdir(parents=True, exist_ok=True)

    query_payload = dict(query_payload)
    query_payload["limit"] = total_papers

    buckets = [f"{bucket_prefix}-{i}" for i in range(1, num_buckets + 1)]
    total_capacity = num_buckets * papers_per_bucket
    region = os.getenv("AWS_REGION", "us-east-2")

    print("=" * 60)
    print(f"Pipeline: persistent PDFs → chunk (batch={pdf_batch_size}) → S3 → delete after upload")
    print(f"  PDF folder: {persistent_dir} (resume-safe: skip download if populated)")
    print(f"  Buckets: {', '.join(buckets)}")
    print("=" * 60)

    # 1) Download only if PDF folder has no PDFs
    all_pdfs = sorted(persistent_dir.glob("*.pdf"))
    if not all_pdfs:
        print("\n[1/3] PDF folder empty — downloading papers from arXiv...")
        existing_titles, existing_arxiv_ids = set(), set()
        if dedup:
            csv_path = Path(papers_csv_path) if papers_csv_path else Path(
                os.getenv("PAPERS_MASTER_CSV", str(script_dir / "papers_master.csv"))
            )
            existing_titles, existing_arxiv_ids = load_existing_papers_from_csv(csv_path)
            if existing_titles or existing_arxiv_ids:
                print(f"      Loaded {len(existing_titles)} titles, {len(existing_arxiv_ids)} arxiv_ids from {csv_path}")
            else:
                if csv_path.exists():
                    print(f"      CSV {csv_path} empty or unreadable")
                else:
                    print(f"      No dedup CSV at {csv_path} — proceeding without dedup")
        papers = fetch_and_download(
            query_payload,
            persistent_dir,
            existing_titles=existing_titles,
            existing_arxiv_ids=existing_arxiv_ids,
            similarity_threshold=similarity_threshold,
        )
        n_downloaded = len(papers)
        print(f"      Downloaded {n_downloaded} papers")
        if n_downloaded < 1:
            print("[!] No papers downloaded. Exiting.")
            return 1
        all_pdfs = sorted(persistent_dir.glob("*.pdf"))
    else:
        print(f"\n[1/3] PDF folder already has {len(all_pdfs)} PDFs — skipping download.")

    # 2) Process in batches of 8: chunk → upload → delete batch from folder
    print("\n[2/3] Chunking and uploading in batches (deleting each batch after upload)...")
    import boto3
    s3 = boto3.client("s3", region_name=region)
    buckets_used: list[str] = []
    batch_num = 0

    while True:
        batch_pdfs = sorted(persistent_dir.glob("*.pdf"))[:pdf_batch_size]
        if not batch_pdfs:
            break

        # Find a bucket with space (existing papers < papers_per_bucket)
        bucket = None
        for b in buckets:
            try:
                ensure_bucket_exists(b, region=region)
            except Exception as e:
                print(f"\n[❌] Failed to create/access bucket {b}: {e}")
                raise
            existing = count_papers_in_bucket(b, prefix=prefix, region=region)
            if existing < papers_per_bucket:
                bucket = b
                if b not in buckets_used:
                    buckets_used.append(b)
                print(f"      [{b}] has {existing}/{papers_per_bucket} papers")
                break
            else:
                print(f"      [{b}] full ({existing} papers) — skipping")
        if bucket is None:
            print("\n[!] All buckets full. Stopping.")
            break

        batch_num += 1
        print(f"\n[📦] Batch {batch_num} ({len(batch_pdfs)} PDFs) → {bucket}")
        temp_root = Path(tempfile.mkdtemp(prefix="pipeline_batch_"))
        temp_dir = temp_root / "batch"
        temp_dir.mkdir()

        try:
            for pdf in batch_pdfs:
                shutil.move(str(pdf), str(temp_dir / pdf.name))
            try:
                chunk_and_upload_s3_only(temp_dir, bucket, prefix=prefix)
            except Exception as e:
                print(f"\n[❌] Failed to chunk/upload batch {batch_num} to {bucket}: {e}")
                print(f"     Error type: {type(e).__name__}")
                if "InvalidAccessKeyId" in str(e) or "AccessDenied" in str(e):
                    print(f"     → AWS authentication failed. Check credentials in .env")
                raise
        finally:
            # Remove batch dir and any chunker output; then remove temp root
            shutil.rmtree(temp_dir, ignore_errors=True)
            output_dir = temp_root / "output"
            if output_dir.exists():
                shutil.rmtree(output_dir, ignore_errors=True)
            shutil.rmtree(temp_root, ignore_errors=True)

    remaining = len(list(persistent_dir.glob("*.pdf")))
    if remaining:
        print(f"\n[ℹ️] {remaining} PDFs left in folder (bucket capacity reached). Re-run to process more or add buckets.")
    else:
        print("\n[✅] All PDFs processed and removed from folder.")

    # 3) Verify
    if verify and buckets_used:
        print("\n[3/3] Verifying chunk structure in S3...")
        for b in buckets_used:
            try:
                resp = s3.list_objects_v2(
                    Bucket=b, Prefix=f"{prefix}/output/", MaxKeys=500
                )
                keys = [o["Key"] for o in resp.get("Contents", [])]
                paper_names = {k.split("/")[2] for k in keys if len(k.split("/")) > 3}
                struct_jsons = [k for k in keys if k.endswith("structured.json")]
                chunks = [k for k in keys if "chunks/text/" in k and k.endswith(".txt")]
                print(f"      {b}: {len(paper_names)} papers, {len(struct_jsons)} structured.json, {len(chunks)} chunks")
                if not struct_jsons or not chunks:
                    print(f"         ⚠️  Chunking may be incomplete")
            except Exception as e:
                print(f"      {b}: verification failed: {e}")

    print("\n" + "=" * 60)
    print("[✅] Pipeline run complete.")
    print(f"     Buckets used: {', '.join(buckets_used)}")
    print("=" * 60)
    return 0
