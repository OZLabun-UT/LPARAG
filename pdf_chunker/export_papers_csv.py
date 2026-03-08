#!/usr/bin/env python3
"""
Export a master list of all papers from S3 buckets with the 'new' prefix.

Scans buckets whose names start with "new" (e.g. new-lwfa-sim-1, new-pwa-2),
reads structured.json for each paper, and outputs a CSV with:
  bucket, paper_folder, title, authors, arxiv_id, date_published, s3_uri

Usage:
  python export_papers_csv.py [--output papers.csv] [--bucket-prefix new]
"""
import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

import boto3


AWS_REGION = os.getenv("AWS_REGION", "us-east-2")
ARXIV_ID_PATTERN = re.compile(r"\b(\d{4}\.\d{4,5})\b")


def list_new_buckets(s3, bucket_prefix: str = "new") -> list[str]:
    """List S3 buckets whose names start with the given prefix."""
    resp = s3.list_buckets()
    return sorted(
        b["Name"] for b in resp.get("Buckets", []) if b["Name"].startswith(bucket_prefix)
    )


def list_structured_json_keys(s3, bucket: str, prefix: str = "kb-data") -> list[str]:
    """List all S3 keys ending with structured.json under the given prefix."""
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            k = obj["Key"]
            if k.endswith("structured.json"):
                keys.append(k)
    return keys


def load_structured_json(s3, bucket: str, key: str) -> dict:
    """Download and parse a structured.json from S3."""
    obj = s3.get_object(Bucket=bucket, Key=key)
    return json.loads(obj["Body"].read())


def extract_title(data: dict) -> str:
    """Extract document title from Docling structured.json."""
    # Root name field
    name = data.get("name") or ""
    # TitleItem in texts (label == "title")
    for t in data.get("texts", []):
        if t.get("label") == "title":
            text = t.get("text", "").strip()
            if text:
                return text
    # First section header or paragraph on page 1
    for t in data.get("texts", []):
        if t.get("label") in ("section_header", "paragraph"):
            prov = t.get("prov", [])
            if prov and prov[0].get("page_no") == 1:
                text = t.get("text", "").strip()
                if text and len(text) > 3:
                    return text
    return name or ""


def extract_authors(data: dict) -> str:
    """Try to extract authors from first-page text (often in a paragraph after title)."""
    authors = []
    for t in data.get("texts", []):
        prov = t.get("prov", [])
        if not prov or prov[0].get("page_no", 0) != 1:
            continue
        label = t.get("label", "")
        text = (t.get("text") or "").strip()
        if not text:
            continue
        # Skip title, section headers, very long blocks (abstract)
        if label == "title":
            continue
        if len(text) > 500:
            continue
        # Authors often: "A. Author1, B. Author2, and C. Author3" or "Author1 et al."
        if re.search(r"\bet\s+al\.?", text, re.I):
            return text
        if re.search(r"\b(and|,)\s+[A-Z]\.?\s+\w+", text) or "," in text:
            authors.append(text)
    return "; ".join(authors[:3]) if authors else ""


def extract_arxiv_id(data: dict, paper_folder: str) -> str:
    """Extract arXiv ID from document text or paper folder name."""
    # Check paper folder (sometimes PDFs are named with arxiv id)
    match = ARXIV_ID_PATTERN.search(paper_folder)
    if match:
        return match.group(1)
    # Search in all text content
    for t in data.get("texts", []):
        text = t.get("text", "") or ""
        match = ARXIV_ID_PATTERN.search(text)
        if match:
            return match.group(1)
    return ""


def extract_date_published(data: dict) -> str:
    """Try to extract publication date from document text."""
    date_pattern = re.compile(
        r"\b(20\d{2}|19\d{2})[-/](0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])\b"
        r"|\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+(20\d{2}|19\d{2})\b"
        r"|\b(20\d{2}|19\d{2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\b",
        re.I,
    )
    for t in data.get("texts", []):
        prov = t.get("prov", [])
        if not prov or prov[0].get("page_no", 0) > 2:
            continue
        text = t.get("text", "") or ""
        match = date_pattern.search(text)
        if match:
            return match.group(0).strip()
    return ""


def load_existing_papers_from_csv(path: Path) -> tuple[set[str], set[str]]:
    """
    Load existing paper titles and arXiv IDs from the master CSV.
    Returns (titles, arxiv_ids) for use in deduplication.
    """
    titles = set()
    arxiv_ids = set()
    if not path.exists():
        return titles, arxiv_ids
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            t = (row.get("title") or "").strip()
            if t:
                titles.add(t)
            aid = (row.get("arxiv_id") or "").strip()
            if aid:
                arxiv_ids.add(aid)
    return titles, arxiv_ids


def extract_paper_metadata(s3, bucket: str, key: str) -> dict:
    """Extract paper metadata from structured.json."""
    paper_folder = "/".join(key.split("/")[:-1])
    try:
        data = load_structured_json(s3, bucket, key)
    except Exception as e:
        return {
            "bucket": bucket,
            "paper_folder": paper_folder,
            "title": "",
            "authors": "",
            "arxiv_id": "",
            "date_published": "",
            "s3_uri": f"s3://{bucket}/{key}",
            "error": str(e),
        }

    return {
        "bucket": bucket,
        "paper_folder": paper_folder,
        "title": extract_title(data),
        "authors": extract_authors(data),
        "arxiv_id": extract_arxiv_id(data, paper_folder),
        "date_published": extract_date_published(data),
        "s3_uri": f"s3://{bucket}/{key}",
        "error": "",
    }


def main():
    parser = argparse.ArgumentParser(
        description="Export papers from S3 buckets with 'new' prefix to CSV."
    )
    parser.add_argument(
        "-o", "--output",
        default="papers_master.csv",
        help="Output CSV path (default: papers_master.csv)",
    )
    parser.add_argument(
        "--bucket-prefix",
        default="new",
        help="Bucket name prefix to filter (default: new)",
    )
    parser.add_argument(
        "--s3-prefix",
        default="kb-data",
        help="S3 prefix under which papers live (default: kb-data)",
    )
    args = parser.parse_args()

    s3 = boto3.client("s3", region_name=AWS_REGION)

    print(f"[🪣] Listing buckets with prefix '{args.bucket_prefix}'...")
    buckets = list_new_buckets(s3, args.bucket_prefix)
    if not buckets:
        print(f"[!] No buckets found starting with '{args.bucket_prefix}'")
        sys.exit(1)
    print(f"    Found: {', '.join(buckets)}")

    all_keys = []
    for bucket in buckets:
        keys = list_structured_json_keys(s3, bucket, args.s3_prefix)
        print(f"    {bucket}: {len(keys)} structured.json")
        for k in keys:
            all_keys.append((bucket, k))

    if not all_keys:
        print("[!] No structured.json files found.")
        sys.exit(1)

    print(f"\n[📄] Extracting metadata from {len(all_keys)} papers...")
    rows = []
    for i, (bucket, key) in enumerate(all_keys, 1):
        if i % 20 == 0 or i == len(all_keys):
            print(f"    {i}/{len(all_keys)}", end="\r")
        row = extract_paper_metadata(s3, bucket, key)
        rows.append(row)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "bucket", "paper_folder", "title", "authors", "arxiv_id",
        "date_published", "s3_uri", "error",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print(f"\n[✅] Wrote {len(rows)} papers to {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
