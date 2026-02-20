#!/usr/bin/env python3
"""
arXiv paper downloader CLI.
Downloads papers matching a query with boolean operators to a local directory (default: pdfs2).

Usage:
  python arxiv_download.py "laser wakefield acceleration" --limit 10
  python arxiv_download.py --query "plasma accelerator" --category physics.plasm-ph --limit 5
  python arxiv_download.py --query-file query.json --output pdfs2

Query JSON format (for --query-file):
  {
    "groups": [{"op": "OR", "terms": [{"field": "ti", "value": "laser wakefield"}, {"field": "abs", "value": "plasma"}]}],
    "and": [{"field": "cat", "value": "physics.plasm-ph"}],
    "not": [{"field": "abs", "value": "simulation"}],
    "limit": 10
  }
"""
import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote_plus

import feedparser
import requests


def compile_arxiv_query(payload: dict) -> str:
    """Build arXiv API query string from boolean payload."""
    parts = []
    for group in payload.get("groups", []):
        if group.get("op") == "OR":
            terms = [f'{t["field"]}:"{t["value"]}"' for t in group.get("terms", [])]
            if terms:
                parts.append("(" + " OR ".join(terms) + ")")
    for t in payload.get("and", []):
        if t.get("field") and t.get("value"):
            parts.append(f'{t["field"]}:"{t["value"]}"')
    for t in payload.get("not", []):
        if t.get("field") and t.get("value"):
            parts.append(f'ANDNOT {t["field"]}:"{t["value"]}"')
    return " AND ".join(parts) if parts else "all"


def fetch_and_download(
    payload: dict,
    download_dir: Path,
) -> list[dict]:
    """Fetch papers from arXiv API and download PDFs to download_dir."""
    limit = int(payload.get("limit", 10))
    compiled = compile_arxiv_query(payload)
    encoded = quote_plus(compiled)

    print(f"[🔎] Query: {compiled}")
    print(f"[🔎] Limit: {limit}")
    print(f"[📂] Output: {download_dir}")

    url = (
        "https://export.arxiv.org/api/query"
        f"?search_query={encoded}&start=0&max_results={limit}"
    )
    feed = feedparser.parse(url)
    download_dir.mkdir(parents=True, exist_ok=True)

    papers = []
    for entry in feed.entries:
        title = entry.title.strip().replace("\n", " ")
        authors = ", ".join(a.name for a in entry.authors)
        abstract = entry.summary.strip().replace("\n", " ")
        arxiv_id = entry.id.split("/abs/")[-1]
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

        safe_name = (
            re.sub(r"[^\w\s-]", "", title).strip().replace(" ", "_") + ".pdf"
        )
        pdf_path = download_dir / safe_name

        try:
            resp = requests.get(pdf_url, timeout=20)
            if resp.status_code == 200:
                with open(pdf_path, "wb") as f:
                    f.write(resp.content)
                print(f"[📄] Saved {pdf_path.name}")
            else:
                print(f"[!] Failed {arxiv_id}: HTTP {resp.status_code}")
        except Exception as e:
            print(f"[!] Error {arxiv_id}: {e}")

        papers.append({
            "title": title,
            "authors": authors,
            "arxiv_id": arxiv_id,
            "pdf_path": str(pdf_path),
        })

    return papers


def main():
    parser = argparse.ArgumentParser(
        description="Download arXiv papers by query to a local directory."
    )
    parser.add_argument(
        "query",
        nargs="?",
        help="Search query (e.g. 'laser wakefield acceleration')",
    )
    parser.add_argument("-q", "--query", dest="query_opt", help="Search query (alternative)")
    parser.add_argument(
        "-c", "--category",
        help="arXiv category (e.g. physics.plasm-ph)",
    )
    parser.add_argument(
        "-l", "--limit",
        type=int,
        default=10,
        help="Max papers to download (default: 10)",
    )
    parser.add_argument(
        "-o", "--output",
        default="pdfs2",
        help="Output directory (default: pdfs2)",
    )
    parser.add_argument(
        "--query-file",
        help="JSON file with full boolean query (groups, and, not, limit)",
    )
    parser.add_argument(
        "--field",
        default="all",
        choices=["all", "ti", "abs", "au", "cat"],
        help="Field for simple query (default: all)",
    )
    args = parser.parse_args()

    # Resolve output path relative to script location
    script_dir = Path(__file__).resolve().parent
    output_dir = Path(args.output)
    if not output_dir.is_absolute():
        output_dir = script_dir / output_dir

    if args.query_file:
        with open(args.query_file, encoding="utf-8") as f:
            payload = json.load(f)
    else:
        query = args.query or args.query_opt
        if not query:
            parser.error("Provide a query or --query-file")
        payload = {
            "groups": [{"op": "OR", "terms": [{"field": args.field, "value": query}]}],
            "and": ([{"field": "cat", "value": args.category}] if args.category else []),
            "not": [],
            "limit": args.limit,
        }

    papers = fetch_and_download(payload, output_dir)
    print(f"\n[✅] Downloaded {len(papers)} papers to {output_dir}")
    return 0 if papers else 1


if __name__ == "__main__":
    sys.exit(main())
