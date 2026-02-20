#!/usr/bin/env python3
"""
LWFA Simulation: "laser wakefield acceleration" AND "simulation"
→ 300 papers → 5 buckets (new-lwfa-sim-1 .. new-lwfa-sim-5)

Uses temp dir; PDFs cleaned up when done.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pdf_chunker.pipeline_runner import run_pipeline

QUERY = {
    "and": [
        {"field": "all", "value": "laser wakefield acceleration"},
        {"field": "all", "value": "simulation"},
    ],
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
        bucket_prefix="new-lwfa-sim",
        total_papers=10 if args.test else 300,
        papers_per_bucket=5 if args.test else 80,
        num_buckets=2 if args.test else 5,
    ))
    except Exception as e:
        print(f"[❌] Pipeline failed: {e}")
        sys.exit(1)
