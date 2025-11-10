#!/usr/bin/env python3
import sys
import boto3
import re
from urllib.parse import urlparse, unquote
from dotenv import load_dotenv

load_dotenv(override=True)
AWS_REGION = "us-east-2"

def parse_s3_url(url: str):
    """
    Parse S3 or presigned URL → (bucket, key)
    Works with:
      - https://bucket.s3.amazonaws.com/key
      - https://bucket.s3.us-east-2.amazonaws.com/key
      - https://s3.us-east-2.amazonaws.com/bucket/key
    """
    parsed = urlparse(url)
    host = parsed.netloc
    path = unquote(parsed.path.lstrip("/"))

    # Case 1: bucket.s3.amazonaws.com or bucket.s3.us-east-2.amazonaws.com
    m1 = re.match(r"^([^.]+)\.s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com$", host)
    if m1:
        bucket = m1.group(1)
        key = path
        return bucket, key

    # Case 2: s3.amazonaws.com/bucket/key or s3.region.amazonaws.com/bucket/key
    m2 = re.match(r"^s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com$", host)
    if m2:
        parts = path.split("/", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid S3 URL path: {path}")
        bucket, key = parts
        return bucket, key

    raise ValueError(f"Cannot parse bucket/key from URL: {url}")


def delete_s3_object(bucket: str, key: str):
    """Delete the specified object from S3."""
    s3 = boto3.client("s3", region_name=AWS_REGION)
    try:
        s3.delete_object(Bucket=bucket, Key=key)
        print(f"[✓] Deleted s3://{bucket}/{key}")
    except Exception as e:
        print(f"[!] Failed to delete s3://{bucket}/{key}: {e}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python s3_delete.py <s3-url>")
        sys.exit(1)

    url = sys.argv[1]
    try:
        bucket, key = parse_s3_url(url)
        delete_s3_object(bucket, key)
    except Exception as e:
        print(f"[!] Error: {e}")
        sys.exit(1)
