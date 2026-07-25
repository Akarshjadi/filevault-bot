"""
Cloudflare R2 Storage Module
Provides async/thread-safe S3-compatible storage using boto3 with multipart uploads and retry logic.
"""
import os
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import boto3
from botocore.exceptions import ClientError, BotoCoreError
from dotenv import load_dotenv

load_dotenv()

# R2 Configuration
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "")
S3_ACCESS_KEY_ID = os.getenv("S3_ACCESS_KEY_ID", "")
S3_SECRET_ACCESS_KEY = os.getenv("S3_SECRET_ACCESS_KEY", "")

# Upload tuning
PART_SIZE = 8 * 1024 * 1024  # 8 MB parts
MAX_CONCURRENT_PARTS = 4
MAX_RETRIES = 3

# Initialize S3 client (thread-safe for concurrent use)
s3_client = None
if all([S3_ENDPOINT_URL, S3_BUCKET_NAME, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY]):
    s3_client = boto3.client(
        's3',
        endpoint_url=S3_ENDPOINT_URL,
        region_name='auto',
        aws_access_key_id=S3_ACCESS_KEY_ID,
        aws_secret_access_key=S3_SECRET_ACCESS_KEY,
    )


def is_configured() -> bool:
    """Check if R2 storage is configured."""
    return s3_client is not None


def verify_connection() -> bool:
    """Verify R2 bucket read/write connectivity."""
    if not is_configured():
        return False
    try:
        s3_client.head_bucket(Bucket=S3_BUCKET_NAME)
        return True
    except (ClientError, BotoCoreError):
        return False


def _calculate_etag(file_bytes: bytes) -> str:
    """Calculate MD5 hash for deduplication."""
    return hashlib.md5(file_bytes).hexdigest()


def upload_file_bytes(file_bytes: bytes, file_unique_id: str, original_filename: str) -> str:
    """
    Upload file bytes to R2 storage.
    
    Uses multipart upload for files > PART_SIZE.
    
    Args:
        file_bytes: Raw file data
        file_unique_id: Telegram file unique ID
        original_filename: Original filename from user
    
    Returns:
        str: The cloud key path where file was stored
    
    Raises:
        RuntimeError: If upload fails
    """
    if not is_configured():
        raise RuntimeError("R2 storage not configured")
    
    safe_filename = os.path.basename(original_filename)
    cloud_key = f"approved/{file_unique_id}/{safe_filename}"
    file_size = len(file_bytes)
    
    try:
        if file_size > PART_SIZE:
            _multipart_upload(cloud_key, file_bytes)
        else:
            s3_client.put_object(
                Bucket=S3_BUCKET_NAME,
                Key=cloud_key,
                Body=file_bytes,
                ContentType='application/octet-stream',
            )
        return cloud_key
    except (ClientError, BotoCoreError) as e:
        raise RuntimeError(f"Failed to upload file to R2: {e}")


def _multipart_upload(cloud_key: str, file_bytes: bytes):
    """Perform multipart upload for large files."""
    file_size = len(file_bytes)
    try:
        mpu = s3_client.create_multipart_upload(
            Bucket=S3_BUCKET_NAME,
            Key=cloud_key,
        )
        upload_id = mpu['UploadId']
        parts = []
        part_number = 1
        lock = threading.Lock()
        
        def upload_part(part_data, part_num):
            for attempt in range(MAX_RETRIES):
                try:
                    resp = s3_client.upload_part(
                        Bucket=S3_BUCKET_NAME,
                        Key=cloud_key,
                        PartNumber=part_num,
                        UploadId=upload_id,
                        Body=part_data,
                    )
                    with lock:
                        parts.append({'PartNumber': part_num, 'ETag': resp['ETag']})
                    return
                except (ClientError, BotoCoreError):
                    if attempt == MAX_RETRIES - 1:
                        raise
                    continue
        
        # Split into parts
        chunk_size = PART_SIZE
        chunks = [file_bytes[i:i + chunk_size] for i in range(0, file_size, chunk_size)]
        
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_PARTS) as executor:
            futures = [
                executor.submit(upload_part, chunk, i + 1)
                for i, chunk in enumerate(chunks)
            ]
            for future in futures:
                future.result()
        
        # Complete multipart upload
        parts.sort(key=lambda x: x['PartNumber'])
        s3_client.complete_multipart_upload(
            Bucket=S3_BUCKET_NAME,
            Key=cloud_key,
            UploadId=upload_id,
            MultipartUpload={'Parts': parts},
        )
    except Exception as e:
        try:
            s3_client.abort_multipart_upload(
                Bucket=S3_BUCKET_NAME,
                Key=cloud_key,
                UploadId=upload_id,
            )
        except Exception:
            pass
        raise RuntimeError(f"Multipart upload failed: {e}")


def generate_download_link(cloud_key: str, expires_in: int = 3600) -> str:
    """
    Generate a presigned URL for downloading a file.
    
    Args:
        cloud_key: The R2 object key
        expires_in: URL expiration time in seconds (default 1 hour)
    
    Returns:
        str: Presigned download URL
    
    Raises:
        RuntimeError: If generation fails
    """
    if not is_configured():
        raise RuntimeError("R2 storage not configured")
    
    try:
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': S3_BUCKET_NAME,
                'Key': cloud_key,
            },
            ExpiresIn=expires_in,
        )
        return url
    except (ClientError, BotoCoreError) as e:
        raise RuntimeError(f"Failed to generate download link: {e}")


def delete_file(cloud_key: str) -> bool:
    """
    Delete a file from R2 storage.
    
    Args:
        cloud_key: The R2 object key
    
    Returns:
        bool: True if deleted successfully
    
    Raises:
        RuntimeError: If deletion fails
    """
    if not is_configured():
        return False
    
    try:
        s3_client.delete_object(
            Bucket=S3_BUCKET_NAME,
            Key=cloud_key,
        )
        return True
    except (ClientError, BotoCoreError) as e:
        raise RuntimeError(f"Failed to delete file from R2: {e}")