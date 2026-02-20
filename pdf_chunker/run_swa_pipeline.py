#!/usr/bin/env python3
"""
Structure Wakefield Acceleration: "structure wakefield acceleration"
→ 300 papers → 5 buckets (new-swa-1 .. new-swa-5)

Uses temp dir; PDFs cleaned up when done.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pdf_chunker.pipeline_runner import run_pipeline

QUERY = {
    "and": [{"field": "all", "value": "structure wakefield acceleration"}],
    "groups": [],
    "not": [],
    "limit": 300,
}

if __name__ == "__main__":
    parser = __import__("argparse").ArgumentParser()
    parser.add_argument("--test", action="store_true", help="10 papers, 2 buckets")
    args = parser.parse_args()
    try:
        sys.exit(run_pipeline(
            query_payload=QUERY,
            bucket_prefix="new-swa",
            total_papers=10 if args.test else 300,
            papers_per_bucket=5 if args.test else 80,
            num_buckets=2 if args.test else 5,
        ))
    except Exception as e:
        print(f"[❌] Pipeline failed: {e}")
        sys.exit(1)
