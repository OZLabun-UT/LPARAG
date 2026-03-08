#!/usr/bin/env python3
"""
Generate statistics and plots from the papers master CSV.

Outputs:
  - Papers per bucket (bar chart)
  - Papers per bucket group (e.g. new-lwfa-sim, new-pwa)
  - Duplicate detection (by title, arxiv_id)
  - Papers with/without arxiv_id
  - Papers with errors
  - Over-capacity buckets (>80 papers)

Usage:
  python papers_stats.py [--csv papers_master.csv] [--output-dir stats_plots]
"""
import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

try:
    import matplotlib.pyplot as plt
    import matplotlib
    matplotlib.use("Agg")
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


def _normalize_title(s: str) -> str:
    """Normalize for duplicate detection."""
    return " ".join((s or "").lower().split())


def load_csv(path: Path) -> pd.DataFrame:
    """Load papers CSV."""
    df = pd.read_csv(path)
    df["title"] = df["title"].fillna("").astype(str)
    df["arxiv_id"] = df["arxiv_id"].fillna("").astype(str).str.strip()
    df["error"] = df["error"].fillna("").astype(str)
    return df


def compute_stats(df: pd.DataFrame) -> dict:
    """Compute all statistics."""
    stats = {}

    # Papers per bucket
    bucket_counts = df["bucket"].value_counts().sort_index()
    stats["papers_per_bucket"] = bucket_counts.to_dict()
    stats["total_papers"] = len(df)
    stats["num_buckets"] = len(bucket_counts)

    # Bucket groups (e.g. new-lwfa-sim-1 -> new-lwfa-sim)
    pattern = re.compile(r"^(.+)-(\d+)$")
    groups = defaultdict(int)
    for bucket in df["bucket"].unique():
        m = pattern.match(bucket)
        if m:
            groups[m.group(1)] += len(df[df["bucket"] == bucket])
    stats["papers_per_group"] = dict(groups)

    # Duplicates by arxiv_id (same arxiv_id in different buckets or rows)
    arxiv_dup = df[df["arxiv_id"] != ""].groupby("arxiv_id").size()
    arxiv_dup = arxiv_dup[arxiv_dup > 1]
    stats["duplicate_arxiv_ids"] = arxiv_dup.to_dict()
    stats["num_duplicate_arxiv"] = len(arxiv_dup)
    stats["papers_with_duplicate_arxiv"] = int(arxiv_dup.sum())

    # Duplicates by normalized title (approximate)
    df["title_norm"] = df["title"].apply(_normalize_title)
    title_counts = df[df["title_norm"] != ""].groupby("title_norm").size()
    title_dup = title_counts[title_counts > 1]
    stats["duplicate_titles"] = title_dup.to_dict()
    stats["num_duplicate_titles"] = len(title_dup)
    stats["papers_with_duplicate_title"] = int(title_dup.sum())

    # Papers with/without arxiv_id
    has_arxiv = (df["arxiv_id"] != "").sum()
    stats["papers_with_arxiv_id"] = int(has_arxiv)
    stats["papers_without_arxiv_id"] = int(len(df) - has_arxiv)

    # Papers with errors
    has_error = (df["error"] != "").sum()
    stats["papers_with_errors"] = int(has_error)

    # Over-capacity buckets (>80)
    over = bucket_counts[bucket_counts > 80]
    stats["over_capacity_buckets"] = over.to_dict()
    stats["num_over_capacity"] = len(over)

    return stats


def print_stats(stats: dict) -> None:
    """Print statistics to stdout."""
    print("\n" + "=" * 60)
    print("PAPERS MASTER CSV — STATISTICS")
    print("=" * 60)
    print(f"\nTotal papers: {stats['total_papers']}")
    print(f"Buckets: {stats['num_buckets']}")
    print(f"\nPapers per bucket:")
    for b, c in sorted(stats["papers_per_bucket"].items()):
        over = " ⚠️ OVER 80" if c > 80 else ""
        print(f"  {b}: {c}{over}")
    print(f"\nPapers per group:")
    for g, c in sorted(stats["papers_per_group"].items()):
        print(f"  {g}: {c}")
    print(f"\nDuplicates:")
    print(f"  By arxiv_id: {stats['num_duplicate_arxiv']} ids ({stats['papers_with_duplicate_arxiv']} papers)")
    print(f"  By title: {stats['num_duplicate_titles']} titles ({stats['papers_with_duplicate_title']} papers)")
    print(f"\nMetadata:")
    print(f"  With arxiv_id: {stats['papers_with_arxiv_id']}")
    print(f"  Without arxiv_id: {stats['papers_without_arxiv_id']}")
    print(f"  With errors: {stats['papers_with_errors']}")
    print(f"\nOver-capacity buckets (>80): {stats['num_over_capacity']}")
    if stats["over_capacity_buckets"]:
        for b, c in stats["over_capacity_buckets"].items():
            print(f"  {b}: {c}")
    print("=" * 60 + "\n")


def save_plots(df: pd.DataFrame, stats: dict, output_dir: Path) -> None:
    """Save plots to output_dir."""
    if not HAS_MATPLOTLIB:
        print("[!] matplotlib not installed. Run: pip install matplotlib")
        return
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1) Papers per bucket (bar chart)
    fig, ax = plt.subplots(figsize=(10, 5))
    bucket_counts = df["bucket"].value_counts().sort_index()
    colors = ["#e74c3c" if c > 80 else "#3498db" for c in bucket_counts.values]
    ax.bar(range(len(bucket_counts)), bucket_counts.values, color=colors)
    ax.axhline(y=80, color="gray", linestyle="--", alpha=0.7, label="Limit (80)")
    ax.set_xticks(range(len(bucket_counts)))
    ax.set_xticklabels(bucket_counts.index, rotation=45, ha="right")
    ax.set_ylabel("Number of papers")
    ax.set_title("Papers per bucket")
    ax.legend()
    plt.tight_layout()
    plt.savefig(output_dir / "papers_per_bucket.png", dpi=150)
    plt.close()

    # 2) Papers per bucket group (pie or bar)
    fig, ax = plt.subplots(figsize=(8, 5))
    groups = stats["papers_per_group"]
    labels = list(groups.keys())
    x = range(len(labels))
    ax.bar(x, list(groups.values()), color="#2ecc71")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.set_ylabel("Number of papers")
    ax.set_title("Papers per bucket group")
    plt.tight_layout()
    plt.savefig(output_dir / "papers_per_group.png", dpi=150)
    plt.close()

    # 3) Metadata: arxiv_id, errors (pie)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    # arxiv_id
    ax = axes[0]
    ax.pie(
        [stats["papers_with_arxiv_id"], stats["papers_without_arxiv_id"]],
        labels=["With arxiv_id", "Without arxiv_id"],
        autopct="%1.1f%%",
        colors=["#3498db", "#95a5a6"],
    )
    ax.set_title("Papers with arxiv_id")
    # errors
    ax = axes[1]
    ax.pie(
        [stats["papers_with_errors"], stats["total_papers"] - stats["papers_with_errors"]],
        labels=["With errors", "No errors"],
        autopct="%1.1f%%",
        colors=["#e74c3c", "#2ecc71"],
    )
    ax.set_title("Papers with extraction errors")
    plt.tight_layout()
    plt.savefig(output_dir / "metadata.png", dpi=150)
    plt.close()

    # 4) Duplicates summary (bar)
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(
        ["Duplicate arxiv_ids", "Duplicate titles"],
        [stats["num_duplicate_arxiv"], stats["num_duplicate_titles"]],
        color=["#e67e22", "#9b59b6"],
    )
    ax.set_ylabel("Count")
    ax.set_title("Duplicate detection")
    plt.tight_layout()
    plt.savefig(output_dir / "duplicates.png", dpi=150)
    plt.close()

    # 5) Over-capacity buckets (if any)
    if stats["over_capacity_buckets"]:
        fig, ax = plt.subplots(figsize=(8, 4))
        over = stats["over_capacity_buckets"]
        labels = list(over.keys())
        x = range(len(labels))
        ax.bar(x, list(over.values()), color="#e74c3c")
        ax.axhline(y=80, color="gray", linestyle="--", alpha=0.7, label="Limit (80)")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=45, ha="right")
        ax.set_ylabel("Number of papers")
        ax.set_title("Over-capacity buckets (>80 papers)")
        ax.legend()
        plt.tight_layout()
        plt.savefig(output_dir / "over_capacity.png", dpi=150)
        plt.close()

    print(f"[✅] Saved plots to {output_dir.resolve()}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate statistics and plots from papers master CSV."
    )
    parser.add_argument(
        "-c", "--csv",
        default="papers_master.csv",
        help="Input CSV path (default: papers_master.csv)",
    )
    parser.add_argument(
        "-o", "--output-dir",
        default="stats_plots",
        help="Output directory for plots (default: stats_plots)",
    )
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    csv_path = Path(args.csv)
    if not csv_path.is_absolute():
        csv_path = script_dir / csv_path
    if not csv_path.exists():
        print(f"[!] CSV not found: {csv_path}")
        sys.exit(1)

    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = script_dir / output_dir

    df = load_csv(csv_path)
    stats = compute_stats(df)
    print_stats(stats)
    save_plots(df, stats, output_dir)

    # Save text summary
    summary_path = output_dir / "summary.txt"
    import io
    buf = io.StringIO()
    for line in [
        "PAPERS MASTER CSV — STATISTICS",
        "=" * 40,
        f"Total papers: {stats['total_papers']}",
        f"Buckets: {stats['num_buckets']}",
        "",
        "Papers per bucket:",
    ]:
        buf.write(line + "\n")
    for b, c in sorted(stats["papers_per_bucket"].items()):
        buf.write(f"  {b}: {c}\n")
    buf.write(f"\nOver-capacity: {stats['num_over_capacity']}\n")
    buf.write(f"Duplicate arxiv_ids: {stats['num_duplicate_arxiv']}\n")
    buf.write(f"Duplicate titles: {stats['num_duplicate_titles']}\n")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(buf.getvalue(), encoding="utf-8")
    print(f"[✅] Saved summary to {summary_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
