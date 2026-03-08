#!/usr/bin/env python3
"""
Rebalance S3 buckets: move excess papers from buckets over the limit to buckets with space.

Scans all buckets with prefix "new" (e.g. new-swa-1, new-pwa-2), groups them by base
(new-swa, new-pwa, new-lwfa-sim, etc.), and for any bucket with > max_papers:
  - Moves excess papers to the next bucket in the same group with space
  - Creates a new bucket if none has space

Usage:
  python rebalance_buckets.py [--max-papers 80] [--dry-run]
"""
import argparse
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

import boto3
from botocore.exceptions import ClientError

# Add parent for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pdf_chunker.s3_push import (
    AWS_REGION,
    count_papers_in_bucket,
    ensure_bucket_exists,
)

S3_PREFIX = "kb-data"
BUCKET_PATTERN = re.compile(r"^(.+)-(\d+)$")


def list_paper_folders(s3, bucket: str, prefix: str = S3_PREFIX) -> list[str]:
    """List paper folder names (e.g. PaperName) under prefix/output/."""
    folders = []
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(
            Bucket=bucket,
            Prefix=f"{prefix}/output/",
            Delimiter="/",
        ):
            for p in page.get("CommonPrefixes", []):
                # p["Prefix"] = "kb-data/output/PaperName/"
                name = p["Prefix"].rstrip("/").split("/")[-1]
                if name:
                    folders.append(name)
    except Exception as e:
        print(f"[!] Could not list papers in {bucket}: {e}")
    return sorted(folders)


def list_objects_under_prefix(s3, bucket: str, prefix: str) -> list[str]:
    """List all object keys under the given prefix."""
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return keys


def copy_object(s3, src_bucket: str, src_key: str, dst_bucket: str, dst_key: str) -> None:
    """Copy a single object from src to dst bucket."""
    copy_source = {"Bucket": src_bucket, "Key": src_key}
    s3.copy_object(CopySource=copy_source, Bucket=dst_bucket, Key=dst_key)


def delete_objects(s3, bucket: str, keys: list[str]) -> None:
    """Delete multiple objects from a bucket (batch of 1000 max)."""
    for i in range(0, len(keys), 1000):
        batch = keys[i : i + 1000]
        objects = [{"Key": k} for k in batch]
        s3.delete_objects(Bucket=bucket, Delete={"Objects": objects})


def group_buckets_by_base(bucket_names: list[str]) -> dict[str, list[tuple[str, int]]]:
    """Group buckets by base prefix, each value: [(bucket_name, index), ...] sorted by index."""
    groups: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for name in bucket_names:
        m = BUCKET_PATTERN.match(name)
        if m:
            base, idx = m.group(1), int(m.group(2))
            groups[base].append((name, idx))
    for base in groups:
        groups[base].sort(key=lambda x: x[1])
    return dict(groups)


def get_next_bucket_with_space(
    s3,
    group_buckets: list[tuple[str, int]],
    max_papers: int,
    region: str,
) -> str | None:
    """Return first bucket in group that has < max_papers, or None."""
    for bucket, _ in group_buckets:
        count = count_papers_in_bucket(bucket, prefix=S3_PREFIX, region=region)
        if count < max_papers:
            return bucket
    return None


def get_or_create_bucket_with_space(
    s3,
    base: str,
    group_buckets: list[tuple[str, int]],
    max_papers: int,
    region: str,
    dry_run: bool,
) -> str | None:
    """Find a bucket with space, or create a new one. Mutates group_buckets when creating."""
    bucket = get_next_bucket_with_space(s3, group_buckets, max_papers, region)
    if bucket:
        return bucket
    # Create new bucket
    next_idx = max(idx for _, idx in group_buckets) + 1
    new_bucket = f"{base}-{next_idx}"
    if dry_run:
        print(f"      [DRY-RUN] Would create bucket {new_bucket}")
        return new_bucket
    ensure_bucket_exists(new_bucket, region=region)
    group_buckets.append((new_bucket, next_idx))
    group_buckets.sort(key=lambda x: x[1])
    return new_bucket


def move_paper(
    s3,
    src_bucket: str,
    paper_folder: str,
    dst_bucket: str,
    prefix: str = S3_PREFIX,
    dry_run: bool = False,
) -> None:
    """Move a paper folder from src_bucket to dst_bucket."""
    paper_prefix = f"{prefix}/output/{paper_folder}/"
    keys = list_objects_under_prefix(s3, src_bucket, paper_prefix)
    if not keys:
        print(f"      [!] No objects under {paper_prefix} in {src_bucket}")
        return
    if dry_run:
        print(f"      [DRY-RUN] Would move {paper_folder} ({len(keys)} objects) → {dst_bucket}")
        return
    for key in keys:
        copy_object(s3, src_bucket, key, dst_bucket, key)
    delete_objects(s3, src_bucket, keys)
    print(f"      Moved {paper_folder} ({len(keys)} objects) → {dst_bucket}")


def rebalance_group(
    s3,
    base: str,
    group_buckets: list[tuple[str, int]],
    max_papers: int,
    region: str,
    dry_run: bool,
) -> int:
    """Rebalance one group. Returns number of papers moved."""
    moved = 0
    for bucket, idx in group_buckets:
        count = count_papers_in_bucket(bucket, prefix=S3_PREFIX, region=region)
        if count <= max_papers:
            continue
        excess = count - max_papers
        folders = list_paper_folders(s3, bucket)
        if len(folders) != count:
            print(f"      [!] Count mismatch in {bucket}: {count} vs {len(folders)} folders")
        to_move = folders[max_papers:]  # papers 81, 82, ...
        print(f"\n[{bucket}] has {count} papers (limit {max_papers}) — moving {len(to_move)} papers")
        for paper_folder in to_move:
            dst = get_or_create_bucket_with_space(
                s3, base, group_buckets, max_papers, region, dry_run
            )
            if not dst:
                print(f"      [!] No destination for {paper_folder}")
                continue
            move_paper(s3, bucket, paper_folder, dst, dry_run=dry_run)
            moved += 1
    return moved


def main():
    parser = argparse.ArgumentParser(
        description="Rebalance new-* S3 buckets: move excess papers to buckets with space."
    )
    parser.add_argument(
        "--max-papers",
        type=int,
        default=80,
        help="Max papers per bucket (default: 80)",
    )
    parser.add_argument(
        "--bucket-prefix",
        default="new",
        help="Bucket name prefix (default: new)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    args = parser.parse_args()

    region = os.getenv("AWS_REGION", AWS_REGION)
    s3 = boto3.client("s3", region_name=region)

    buckets = [
        b["Name"]
        for b in s3.list_buckets().get("Buckets", [])
        if b["Name"].startswith(args.bucket_prefix)
    ]
    if not buckets:
        print(f"[!] No buckets found with prefix '{args.bucket_prefix}'")
        sys.exit(1)

    groups = group_buckets_by_base(buckets)
    print(f"[🪣] Found {len(buckets)} buckets in {len(groups)} groups")
    for base, group in sorted(groups.items()):
        print(f"   {base}: {[b for b, _ in group]}")

    total_moved = 0
    for base, group_buckets in sorted(groups.items()):
        moved = rebalance_group(
            s3, base, group_buckets, args.max_papers, region, args.dry_run
        )
        total_moved += moved

    if args.dry_run:
        print(f"\n[DRY-RUN] Would have moved {total_moved} papers")
    else:
        print(f"\n[✅] Moved {total_moved} papers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
