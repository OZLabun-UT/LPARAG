#!/usr/bin/env python3
"""
Quick diagnostic script to check AWS credentials configuration.
Run this to verify your AWS credentials are set up correctly.
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load .env from repo root
repo_root = Path(__file__).resolve().parent.parent
env_file = repo_root / ".env"
load_dotenv(env_file, override=True)

print("=" * 60)
print("AWS Credentials Diagnostic")
print("=" * 60)

# Check environment variables
access_key = os.getenv("AWS_ACCESS_KEY_ID")
secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
region = os.getenv("AWS_REGION", "us-east-2")

print(f"\n[1] Environment variables:")
print(f"    AWS_ACCESS_KEY_ID: {'SET' if access_key else 'NOT SET'}")
if access_key:
    print(f"                      (starts with: {access_key[:8]}...)")
print(f"    AWS_SECRET_ACCESS_KEY: {'SET' if secret_key else 'NOT SET'}")
print(f"    AWS_REGION: {region}")

if not access_key or not secret_key:
    print("\n[❌] Missing credentials!")
    print(f"     → Check your .env file at: {env_file}")
    print(f"     → Required: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY")
    sys.exit(1)

# Test boto3 connection
print(f"\n[2] Testing AWS connection...")
try:
    import boto3
    s3 = boto3.client("s3", region_name=region)
    buckets = s3.list_buckets()
    print(f"    ✓ Connection successful!")
    print(f"    ✓ Found {len(buckets.get('Buckets', []))} buckets")
    
    # Try to create a test bucket (will fail if bucket exists, but that's OK)
    from botocore.exceptions import ClientError
    test_bucket = "test-auth-check-bucket-does-not-exist-12345"
    try:
        s3.create_bucket(
            Bucket=test_bucket,
            CreateBucketConfiguration={"LocationConstraint": region}
        )
        print(f"    ✓ Can create buckets")
        # Clean up
        s3.delete_bucket(Bucket=test_bucket)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "BucketAlreadyOwnedByYou":
            print(f"    ✓ Can create buckets (test bucket existed)")
        elif code in ("InvalidAccessKeyId", "SignatureDoesNotMatch", "AccessDenied"):
            print(f"    ❌ Authentication failed: {code}")
            print(f"       → Your credentials are invalid or expired")
            sys.exit(1)
        else:
            print(f"    ✓ Permissions OK (expected error: {code})")
    
except ImportError:
    print("    ❌ boto3 not installed")
    print("       → Run: pip install boto3")
    sys.exit(1)
except Exception as e:
    error_type = type(e).__name__
    error_msg = str(e)
    print(f"    ❌ Connection failed: {error_type}")
    print(f"       {error_msg}")
    
    if "InvalidAccessKeyId" in error_msg:
        print("\n[❌] Invalid Access Key ID")
        print("     → Your AWS_ACCESS_KEY_ID in .env is incorrect or expired")
        print("     → Get new credentials from AWS IAM console")
    elif "SignatureDoesNotMatch" in error_msg:
        print("\n[❌] Invalid Secret Access Key")
        print("     → Your AWS_SECRET_ACCESS_KEY in .env is incorrect")
        print("     → Get new credentials from AWS IAM console")
    elif "NoCredentialsError" in error_type:
        print("\n[❌] No credentials found")
        print("     → boto3 couldn't find credentials")
        print("     → Ensure .env is loaded (check load_dotenv is called)")
    else:
        print(f"\n[❌] Unexpected error: {error_type}")
        print("     → Check AWS region, network connection, etc.")
    
    sys.exit(1)

print("\n" + "=" * 60)
print("[✅] AWS credentials are configured correctly!")
print("=" * 60)
