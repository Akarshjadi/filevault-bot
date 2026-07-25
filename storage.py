"""
Cloudflare R2 Storage Module — DPDP Compliant
Dual-bucket storage: adult files in 'approved/', minor files in 'minors-encrypted/'
"""
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.exceptions import ClientError, BotoCoreError
from dotenv import load_dotenv

load_dotenv()

# R2 Configuration
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "")
R2_MAIN_BUCKET = os.getenv("R2_MAIN_BUCKET", "filevault-approved")
R2_MINORS_BUCKET = os.getenv("R2_MINORS_BUCKET", "filevault-minors-encrypted")
S3_ACCESS_KEY_ID = os.getenv("S3_ACCESS_KEY_ID", "")
S3_SECRET_ACCESS_KEY = os.getenv("S3_SECRET_ACCESS_KEY", "")

# Upload tuning
PART_SIZE = 8 * 1024 * 1024  # 8 MB parts
MAX_CONCURRENT_PARTS = 4
MAX_RETRIES = 3

# Initialize S3 client (thread-safe)
s3_client = None
if all([S3_ENDPOINT_URL, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY]):
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
        s3_client.head_bucket(Bucket=R2_MAIN_BUCKET)
        return True
    except (ClientError, BotoCoreError):
        return False


def upload_file(file_bytes: bytes, cloud_key: str, is_minor: bool = False) -> str:
    """
    Upload file to R2 with appropriate bucket based on minor status.
    
    Args:
        file_bytes: Raw file data
        cloud_key: Relative path within bucket
        is_minor: If True, uses minors-encrypted bucket
    
    Returns:
        str: Full S3 key
    
    Raises:
        RuntimeError: If upload fails
    """
    if not is_configured():
        raise RuntimeError("R2 storage not configured")
    
    bucket = R2_MINORS_BUCKET if is_minor else R2_MAIN_BUCKET
    file_size = len(file_bytes)
    
    try:
        if file_size > PART_SIZE:
            _multipart_upload(bucket, cloud_key, file_bytes)
        else:
            s3_client.put_object(
                Bucket=bucket,
                Key=cloud_key,
                Body=file_bytes,
                ContentType='application/octet-stream',
            )
        return cloud_key
    except (ClientError, BotoCoreError) as e:
        raise RuntimeError(f"Failed to upload file to R2: {e}")


def _multipart_upload(bucket: str, cloud_key: str, file_bytes: bytes):
    """Perform multipart upload for large files."""
    file_size = len(file_bytes)
    try:
        mpu = s3_client.create_multipart_upload(
            Bucket=bucket,
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
                        Bucket=bucket,
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
        
        chunk_size = PART_SIZE
        chunks = [file_bytes[i:i + chunk_size] for i in range(0, file_size, chunk_size)]
        
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_PARTS) as executor:
            futures = [
                executor.submit(upload_part, chunk, i + 1)
                for i, chunk in enumerate(chunks)
            ]
            for future in futures:
                future.result()
        
        parts.sort(key=lambda x: x['PartNumber'])
        s3_client.complete_multipart_upload(
            Bucket=bucket,
            Key=cloud_key,
            UploadId=upload_id,
            MultipartUpload={'Parts': parts},
        )
    except Exception as e:
        try:
            s3_client.abort_multipart_upload(
                Bucket=bucket,
                Key=cloud_key,
                UploadId=upload_id,
            )
        except Exception:
            pass
        raise RuntimeError(f"Multipart upload failed: {e}")


def generate_download_link(cloud_key: str, is_minor: bool = False, expires_in: int = 3600) -> str:
    """
    Generate a presigned URL for downloading a file.
    
    Args:
        cloud_key: The R2 object key
        is_minor: If True, uses minors-encrypted bucket
        expires_in: URL expiration time in seconds
    
    Returns:
        str: Presigned download URL
    
    Raises:
        RuntimeError: If generation fails
    """
    if not is_configured():
        raise RuntimeError("R2 storage not configured")
    
    bucket = R2_MINORS_BUCKET if is_minor else R2_MAIN_BUCKET
    try:
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': bucket,
                'Key': cloud_key,
            },
            ExpiresIn=expires_in,
        )
        return url
    except (ClientError, BotoCoreError) as e:
        raise RuntimeError(f"Failed to generate download link: {e}")


def delete_file(cloud_key: str, is_minor: bool = False) -> bool:
    """
    Delete a file from R2 storage.
    
    Args:
        cloud_key: The R2 object key
        is_minor: If True, uses minors-encrypted bucket
    
    Returns:
        bool: True if deleted successfully
    """
    if not is_configured():
        return False
    
    bucket = R2_MINORS_BUCKET if is_minor else R2_MAIN_BUCKET
    try:
        s3_client.delete_object(
            Bucket=bucket,
            Key=cloud_key,
        )
        return True
    except (ClientError, BotoCoreError):
        return False


def list_files(prefix: str, is_minor: bool = False) -> list[str]:
    """
    List files in R2 bucket with given prefix.
    
    Args:
        prefix: Key prefix to filter
        is_minor: If True, uses minors-encrypted bucket
    
    Returns:
        list[str]: List of object keys
    """
    if not is_configured():
        return []
    
    bucket = R2_MINORS_BUCKET if is_minor else R2_MAIN_BUCKET
    try:
        response = s3_client.list_objects_v2(
            Bucket=bucket,
            Prefix=prefix
        )
        return [obj['Key'] for obj in response.get('Contents', [])]
    except (ClientError, BotoCoreError):
        return []