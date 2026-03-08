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
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote_plus

import feedparser
import requests


def _normalize_title(s: str) -> str:
    """Normalize title for comparison: lowercase, collapse whitespace."""
    return " ".join((s or "").lower().split())


def _title_similarity(a: str, b: str) -> float:
    """Return similarity ratio 0-1 (1 = identical)."""
    na, nb = _normalize_title(a), _normalize_title(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def _is_duplicate(
    title: str,
    arxiv_id: str,
    existing_titles: set[str],
    existing_arxiv_ids: set[str],
    similarity_threshold: float,
) -> tuple[bool, str]:
    """
    Check if paper is a duplicate. Returns (is_dup, reason).
    """
    if arxiv_id and arxiv_id in existing_arxiv_ids:
        return True, f"arxiv_id {arxiv_id}"
    for ext in existing_titles:
        if _title_similarity(title, ext) >= similarity_threshold:
            return True, f"title match: {ext[:60]}..."
    return False, ""


def _expand_term(field: str, value: str, phrase: bool = False) -> str:
    """
    Expand a term for arXiv API. Multi-word values are AND'd as separate words
    (matching arXiv website behavior) unless phrase=True for exact phrase match.
    """
    val = (value or "").strip()
    if not val:
        return ""
    if phrase:
        return f'{field}:"{val}"'
    words = val.split()
    if len(words) == 1:
        return f'{field}:{words[0]}'
    # Multi-word: AND each word (broader match, like arXiv website)
    return " AND ".join(f"{field}:{w}" for w in words)


def compile_arxiv_query(payload: dict, phrase_search: bool = False) -> str:
    """
    Build arXiv API query string from boolean payload.

    By default, multi-word values like "structure wakefield acceleration" are
    expanded to AND of words (all:structure AND all:wakefield AND all:acceleration),
    matching arXiv website behavior. Set phrase_search=True for exact phrase match.
    """
    parts = []
    for group in payload.get("groups", []):
        if group.get("op") == "OR":
            terms = [
                _expand_term(t["field"], t["value"], phrase=True)
                for t in group.get("terms", [])
                if t.get("field") and t.get("value")
            ]
            if terms:
                parts.append("(" + " OR ".join(terms) + ")")
    for t in payload.get("and", []):
        if t.get("field") and t.get("value"):
            parts.append(_expand_term(t["field"], t["value"], phrase=phrase_search))
    and_parts = [p for p in parts]
    not_parts = []
    for t in payload.get("not", []):
        if t.get("field") and t.get("value"):
            expanded = _expand_term(t["field"], t["value"], phrase=phrase_search)
            if expanded:
                not_parts.append(f"({expanded})" if " AND " in expanded else expanded)
    result = " AND ".join(and_parts) if and_parts else ""
    if not_parts:
        result = (result + " ANDNOT " + " ANDNOT ".join(not_parts)) if result else "ANDNOT " + " ANDNOT ".join(not_parts)
    return result if result else "all"


def fetch_and_download(
    payload: dict,
    download_dir: Path,
    *,
    existing_titles: set[str] | None = None,
    existing_arxiv_ids: set[str] | None = None,
    similarity_threshold: float = 0.85,
) -> list[dict]:
    """
    Fetch papers from arXiv API and download PDFs to download_dir.

    If existing_titles or existing_arxiv_ids are provided, skips papers that
    match (exact arxiv_id match, or title similarity >= similarity_threshold).
    """
    limit = int(payload.get("limit", 10))
    compiled = compile_arxiv_query(payload)
    encoded = quote_plus(compiled)

    existing_titles = existing_titles or set()
    existing_arxiv_ids = existing_arxiv_ids or set()
    dedup = bool(existing_titles or existing_arxiv_ids)

    print(f"[🔎] Query: {compiled}")
    print(f"[🔎] Limit: {limit}")
    print(f"[📂] Output: {download_dir}")
    if dedup:
        print(f"[🔄] Dedup: {len(existing_titles)} titles, {len(existing_arxiv_ids)} arxiv_ids (threshold={similarity_threshold})")

    url = (
        "https://export.arxiv.org/api/query"
        f"?search_query={encoded}&start=0&max_results={limit}"
    )
    feed = feedparser.parse(url)
    download_dir.mkdir(parents=True, exist_ok=True)

    papers = []
    skipped = 0
    for entry in feed.entries:
        title = entry.title.strip().replace("\n", " ")
        authors = ", ".join(a.name for a in entry.authors)
        abstract = entry.summary.strip().replace("\n", " ")
        arxiv_id = entry.id.split("/abs/")[-1]
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

        if dedup:
            is_dup, reason = _is_duplicate(
                title, arxiv_id,
                existing_titles, existing_arxiv_ids,
                similarity_threshold,
            )
            if is_dup:
                print(f"[⏭] Skipped duplicate ({reason}): {title[:60]}...")
                skipped += 1
                continue

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

    if dedup and skipped:
        print(f"[ℹ️] Skipped {skipped} duplicates, downloaded {len(papers)} new papers")
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
