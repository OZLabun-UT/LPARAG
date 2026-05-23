"""
RAG Query Logger
----------------
Writes structured JSONL log entries locally and uploads each daily file to S3.
Designed to never raise exceptions that could break the API — all errors
are printed to stderr and swallowed.
"""

import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import boto3
from dotenv import load_dotenv

load_dotenv(override=True)


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class RagLogger:
    def __init__(self):
        log_dir_env = os.getenv("LOG_DIR", "")
        if log_dir_env:
            self.log_dir = Path(log_dir_env)
            if not self.log_dir.is_absolute():
                self.log_dir = _PROJECT_ROOT / self.log_dir
        else:
            self.log_dir = _PROJECT_ROOT / "logs"

        self.s3_bucket = os.getenv("LOG_S3_BUCKET", "")
        self.s3_prefix = os.getenv("LOG_S3_PREFIX", "query-logs").strip("/")
        self._lock = threading.Lock()

        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            print(f"[Logger] Writing logs to {self.log_dir}")
        except Exception as e:
            print(f"[Logger] Could not create log dir {self.log_dir}: {e}", file=sys.stderr)

        if self.s3_bucket:
            try:
                region = os.getenv("AWS_REGION", "us-east-2")
                self._s3 = boto3.client("s3", region_name=region)
            except Exception as e:
                print(f"[Logger] Could not create S3 client: {e}", file=sys.stderr)
                self._s3 = None
        else:
            self._s3 = None

    def log(self, entry: dict) -> None:
        """Append entry to today's JSONL file and upload to S3 in a background thread."""
        try:
            now = datetime.now(timezone.utc)
            entry.setdefault("timestamp", now.isoformat())
            local_path = self.log_dir / f"queries_{now:%Y%m%d}.jsonl"

            with self._lock:
                with open(local_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

            print(f"[Logger] Entry written → {local_path.name}")

            if self._s3 and self.s3_bucket:
                threading.Thread(
                    target=self._upload_to_s3,
                    args=(local_path, now),
                    daemon=True,
                ).start()

        except Exception as e:
            print(f"[Logger] Failed to write log entry: {e}", file=sys.stderr)

    def _upload_to_s3(self, local_path: Path, dt: datetime) -> None:
        """Upload the full daily log file to S3. Called from a background thread."""
        try:
            s3_key = f"{self.s3_prefix}/{dt:%Y/%m/%d}/queries_{dt:%Y%m%d}.jsonl"
            with open(local_path, "rb") as f:
                self._s3.put_object(
                    Bucket=self.s3_bucket,
                    Key=s3_key,
                    Body=f.read(),
                    ContentType="application/x-ndjson",
                )
            print(f"[Logger] Uploaded to s3://{self.s3_bucket}/{s3_key}")
        except Exception as e:
            print(f"[Logger] S3 upload failed ({local_path.name}): {e}", file=sys.stderr)
