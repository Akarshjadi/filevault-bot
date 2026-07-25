"""
Cloudflare R2 Storage Module — Evidence Vault Multi-Bucket System
DPDP Act 2023 Compliant with separate buckets for different data classifications.

Bucket Structure:
- evidence-originals: Private. Original unaltered uploads. Only bot role can access.
- evidence-exif: Private. Extracted EXIF data. Auto-delete after 1 year.
- evidence-processing: Private. Working copies during face detection/blur.
- evidence-published: Private. Final approved copies served via signed URLs.
"""
import os
import hashlib
import logging
from typing import Optional, Tuple
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, BotoCoreError, NoCredentialsError
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# R2 Configuration
R2_CONFIG = {
    'endpoint_url': os.getenv("S3_ENDPOINT_URL", ""),
    'access_key_id': os.getenv("S3_ACCESS_KEY_ID", ""),
    'secret_access_key': os.getenv("S3_SECRET_ACCESS_KEY", ""),
    'region': 'auto',
}

# Bucket names
BUCKET_ORIGINALS = os.getenv("R2_BUCKET_ORIGINALS", "evidence-originals")
BUCKET_EXIF = os.getenv("R2_BUCKET_EXIF", "evidence-exif")
BUCKET_PROCESSING = os.getenv("R2_BUCKET_PROCESSING", "evidence-processing")
BUCKET_PUBLISHED = os.getenv("R2_BUCKET_PUBLISHED", "evidence-published")

# Upload tuning
PART_SIZE = 8 * 1024 * 1024  # 8 MB parts
MAX_CONCURRENT_PARTS = 4
MAX_RETRIES = 3
PRESIGNED_URL_EXPIRY = 3600  # 1 hour


class R2Storage:
    """R2 Storage client with multi-bucket support."""
    
    def __init__(self):
        self._client = None
        self._configured = False
        self._initialize_client()
    
    def _initialize_client(self):
        """Initialize S3/R2 client if credentials are available."""
        if all([
            R2_CONFIG['endpoint_url'],
            R2_CONFIG['access_key_id'],
            R2_CONFIG['secret_access_key']
        ]):
            try:
                self._client = boto3.client(
                    's3',
                    endpoint_url=R2_CONFIG['endpoint_url'],
                    region_name=R2_CONFIG['region'],
                    aws_access_key_id=R2_CONFIG['access_key_id'],
                    aws_secret_access_key=R2_CONFIG['secret_access_key'],
                )
                self._configured = True
                logger.info("R2 storage client initialized")
            except Exception as e:
                logger.error(f"Failed to initialize R2 client: {e}")
                self._configured = False
        else:
            logger.warning("R2 storage not configured (missing credentials)")
            self._configured = False
    
    def is_configured(self) -> bool:
        """Check if R2 storage is configured and initialized."""
        return self._configured and self._client is not None
    
    def verify_connection(self) -> Tuple[bool, str]:
        """
        Verify R2 buckets are accessible.
        Returns: (success: bool, message: str)
        """
        if not self.is_configured():
            return False, "R2 not configured"
        
        buckets = {
            'originals': BUCKET_ORIGINALS,
            'exif': BUCKET_EXIF,
            'processing': BUCKET_PROCESSING,
            'published': BUCKET_PUBLISHED,
        }
        
        results = []
        for name, bucket in buckets.items():
            try:
                self._client.head_bucket(Bucket=bucket)
                results.append(f"✓ {name}: {bucket}")
            except ClientError as e:
                error_code = e.response['Error']['Code']
                if error_code == '404':
                    results.append(f"✗ {name}: Bucket not found: {bucket}")
                else:
                    results.append(f"✗ {name}: {str(e)}")
            except Exception as e:
                results.append(f"✗ {name}: {str(e)}")
        
        message = "\n".join(results)
        success = all("✓" in r for r in results)
        return success, message
    
    def compute_hash(self, file_bytes: bytes) -> str:
        """Compute SHA-256 hash of file content."""
        return hashlib.sha256(file_bytes).hexdigest()
    
    def get_object_path(self, bucket_type: str, identifier: str, filename: str = "") -> str:
        """
        Generate object path for R2.
        
        Args:
            bucket_type: One of 'originals', 'exif', 'processing', 'published'
            identifier: Submission hash, UUID, or other unique identifier
            filename: Optional filename extension
        """
        if filename:
            return f"{identifier}/{filename}"
        return identifier
    
    def upload_original(self, file_bytes: bytes, file_hash: str, filename: str) -> str:
        """
        Upload original unaltered file to evidence-originals bucket.
        
        Args:
            file_bytes: Raw file data
            file_hash: SHA-256 hash of file
            filename: Original filename with extension
        
        Returns:
            str: Full S3 key (r2:// evidence-originals/{hash}/{filename})
        
        Raises:
            RuntimeError: If upload fails
        """
        if not self.is_configured():
            raise RuntimeError("R2 storage not configured")
        
        key = self.get_object_path('originals', file_hash, filename)
        return self._upload(BUCKET_ORIGINALS, key, file_bytes, metadata={
            'hash': file_hash,
            'classification': 'original',
            'retention': '90days',
        })
    
    def upload_exif(self, file_hash: str, exif_data: bytes) -> str:
        """
        Upload extracted EXIF data to evidence-exif bucket.
        
        Args:
            file_hash: SHA-256 hash of parent file
            exif_data: JSON/bytes of EXIF information
        
        Returns:
            str: Full S3 key
        """
        if not self.is_configured():
            raise RuntimeError("R2 storage not configured")
        
        key = self.get_object_path('exif', file_hash, 'exif.json')
        return self._upload(BUCKET_EXIF, key, exif_data, metadata={
            'parent_hash': file_hash,
            'classification': 'exif',
            'retention': '1year',
        })
    
    def upload_processing(self, submission_id: str, filename: str, file_bytes: bytes) -> str:
        """
        Upload working copy to evidence-processing bucket.
        
        Args:
            submission_id: UUID of submission
            filename: Filename (e.g., 'blurred_initial.mp4')
            file_bytes: Processed file data
        
        Returns:
            str: Full S3 key
        """
        if not self.is_configured():
            raise RuntimeError("R2 storage not configured")
        
        key = self.get_object_path('processing', submission_id, filename)
        return self._upload(BUCKET_PROCESSING, key, file_bytes, metadata={
            'submission_id': submission_id,
            'classification': 'processing',
        })
    
    def upload_published(self, submission_id: str, file_bytes: bytes) -> str:
        """
        Upload final approved copy to evidence-published bucket.
        
        Args:
            submission_id: UUID of submission
            file_bytes: Final processed file with approved unblurred faces
        
        Returns:
            str: Full S3 key
        """
        if not self.is_configured():
            raise RuntimeError("R2 storage not configured")
        
        # Determine file extension based on submission (in real app, check DB)
        ext = 'mp4'  # Default to video
        key = self.get_object_path('published', submission_id, f'final.{ext}')
        return self._upload(BUCKET_PUBLISHED, key, file_bytes, metadata={
            'submission_id': submission_id,
            'classification': 'published',
        })
    
    def _upload(self, bucket: str, key: str, data: bytes, metadata: Optional[dict] = None) -> str:
        """
        Internal upload method with multipart support.
        
        Args:
            bucket: R2 bucket name
            key: Object key/path
            data: File bytes
            metadata: Optional metadata dict
        
        Returns:
            str: Full S3 key
        
        Raises:
            RuntimeError: If upload fails
        """
        if not self.is_configured():
            raise RuntimeError("R2 storage not configured")
        
        file_size = len(data)
        
        try:
            if file_size > PART_SIZE:
                self._multipart_upload(bucket, key, data, metadata)
            else:
                kwargs = {
                    'Bucket': bucket,
                    'Key': key,
                    'Body': data,
                    'ContentType': 'application/octet-stream',
                }
                if metadata:
                    kwargs['Metadata'] = metadata
                
                self._client.put_object(**kwargs)
            
            logger.info(f"Uploaded: r2://{bucket}/{key} ({file_size} bytes)")
            return f"r2://{bucket}/{key}"
        
        except (ClientError, BotoCoreError, NoCredentialsError) as e:
            logger.error(f"Upload failed r2://{bucket}/{key}: {e}")
            raise RuntimeError(f"Failed to upload to R2: {e}")
    
    def _multipart_upload(self, bucket: str, key: str, data: bytes, metadata: Optional[dict] = None):
        """Perform multipart upload for large files."""
        file_size = len(data)
        try:
            mpu = self._client.create_multipart_upload(
                Bucket=bucket,
                Key=key,
                Metadata=metadata or {},
            )
            upload_id = mpu['UploadId']
            parts = []
            part_number = 1
            
            # Create chunks
            chunks = [data[i:i + PART_SIZE] for i in range(0, file_size, PART_SIZE)]
            
            import threading
            from concurrent.futures import ThreadPoolExecutor
            
            lock = threading.Lock()
            
            def upload_part(part_data, part_num):
                for attempt in range(MAX_RETRIES):
                    try:
                        resp = self._client.upload_part(
                            Bucket=bucket,
                            Key=key,
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
            
            with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_PARTS) as executor:
                futures = [
                    executor.submit(upload_part, chunk, i + 1)
                    for i, chunk in enumerate(chunks)
                ]
                for future in futures:
                    future.result()
            
            parts.sort(key=lambda x: x['PartNumber'])
            
            self._client.complete_multipart_upload(
                Bucket=bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={'Parts': parts},
            )
        
        except Exception as e:
            try:
                self._client.abort_multipart_upload(
                    Bucket=bucket,
                    Key=key,
                    UploadId=upload_id,
                )
            except Exception:
                pass
            raise RuntimeError(f"Multipart upload failed: {e}")
    
    def download(self, bucket: str, key: str) -> bytes:
        """
        Download file from R2.
        
        Args:
            bucket: R2 bucket name
            key: Object key
        
        Returns:
            bytes: File content
        
        Raises:
            RuntimeError: If download fails
        """
        if not self.is_configured():
            raise RuntimeError("R2 storage not configured")
        
        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
            return response['Body'].read()
        except (ClientError, BotoCoreError) as e:
            logger.error(f"Download failed r2://{bucket}/{key}: {e}")
            raise RuntimeError(f"Failed to download from R2: {e}")
    
    def get_signed_url(self, bucket: str, key: str, expires_in: int = PRESIGNED_URL_EXPIRY) -> str:
        """
        Generate presigned URL for temporary access.
        
        Args:
            bucket: R2 bucket name
            key: Object key
            expires_in: URL expiration in seconds (default 1 hour)
        
        Returns:
            str: Presigned URL
        """
        if not self.is_configured():
            raise RuntimeError("R2 storage not configured")
        
        try:
            url = self._client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': bucket,
                    'Key': key,
                },
                ExpiresIn=expires_in,
            )
            return url
        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to generate presigned URL: {e}")
            raise RuntimeError(f"Failed to generate presigned URL: {e}")
    
    def delete(self, bucket: str, key: str) -> bool:
        """
        Delete object from R2.
        
        Args:
            bucket: R2 bucket name
            key: Object key
        
        Returns:
            bool: True if deleted successfully
        """
        if not self.is_configured():
            return False
        
        try:
            self._client.delete_object(Bucket=bucket, Key=key)
            logger.info(f"Deleted: r2://{bucket}/{key}")
            return True
        except (ClientError, BotoCoreError) as e:
            logger.error(f"Delete failed r2://{bucket}/{key}: {e}")
            return False
    
    def exists(self, bucket: str, key: str) -> bool:
        """Check if object exists in R2."""
        if not self.is_configured():
            return False
        
        try:
            self._client.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError:
            return False
    
    def list_objects(self, bucket: str, prefix: str = "") -> list:
        """
        List objects in bucket with optional prefix.
        
        Args:
            bucket: R2 bucket name
            prefix: Key prefix filter
        
        Returns:
            list: List of object keys
        """
        if not self.is_configured():
            return []
        
        try:
            paginator = self._client.get_paginator('list_objects_v2')
            objects = []
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get('Contents', []):
                    objects.append(obj['Key'])
            return objects
        except (ClientError, BotoCoreError) as e:
            logger.error(f"List objects failed: {e}")
            return []
    
    def get_metadata(self, bucket: str, key: str) -> Optional[dict]:
        """Get object metadata."""
        if not self.is_configured():
            return None
        
        try:
            response = self._client.head_object(Bucket=bucket, Key=key)
            return {
                'content_type': response.get('ContentType'),
                'content_length': response.get('ContentLength'),
                'metadata': response.get('Metadata', {}),
                'last_modified': response.get('LastModified'),
            }
        except (ClientError, BotoCoreError):
            return None


# Global singleton instance
_r2_storage = R2Storage()


def get_r2_storage() -> R2Storage:
    """Get the global R2 storage instance."""
    return _r2_storage


# Convenience functions that use the global instance
def upload_fileobj(file_bytes: bytes, r2_path: str, bucket_type: str = 'processing') -> str:
    """
    Upload file bytes to appropriate R2 bucket.
    
    Args:
        file_bytes: File content
        r2_path: R2 object path (e.g., "submission_id/filename.mp4")
        bucket_type: One of 'originals', 'exif', 'processing', 'published'
    
    Returns:
        str: Full R2 URI (r2://bucket/path)
    """
    bucket_map = {
        'originals': BUCKET_ORIGINALS,
        'exif': BUCKET_EXIF,
        'processing': BUCKET_PROCESSING,
        'published': BUCKET_PUBLISHED,
    }
    bucket = bucket_map.get(bucket_type, BUCKET_PROCESSING)
    return _r2_storage._upload(bucket, r2_path, file_bytes)


def download_file(r2_path: str, bucket_type: str = 'processing') -> bytes:
    """Download file from R2."""
    bucket_map = {
        'originals': BUCKET_ORIGINALS,
        'exif': BUCKET_EXIF,
        'processing': BUCKET_PROCESSING,
        'published': BUCKET_PUBLISHED,
    }
    bucket = bucket_map.get(bucket_type, BUCKET_PROCESSING)
    # Extract key from r2_path if full URI, otherwise use as-is
    if r2_path.startswith('r2://'):
        parts = r2_path.replace('r2://', '').split('/', 1)
        bucket = parts[0]
        key = parts[1] if len(parts) > 1 else parts[0]
    else:
        key = r2_path
    
    return _r2_storage.download(bucket, key)


def get_presigned_url(r2_path: str, bucket_type: str = 'published', expires_in: int = 3600) -> str:
    """Get presigned URL for file access."""
    bucket_map = {
        'originals': BUCKET_ORIGINALS,
        'exif': BUCKET_EXIF,
        'processing': BUCKET_PROCESSING,
        'published': BUCKET_PUBLISHED,
    }
    bucket = bucket_map.get(bucket_type, BUCKET_PUBLISHED)
    
    # Extract key from r2_path if full URI
    if r2_path.startswith('r2://'):
        parts = r2_path.replace('r2://', '').split('/', 1)
        bucket = parts[0]
        key = parts[1] if len(parts) > 1 else parts[0]
    else:
        key = r2_path
    
    return _r2_storage.get_signed_url(bucket, key, expires_in)


def delete_file(r2_path: str, bucket_type: str = 'processing') -> bool:
    """Delete file from R2."""
    bucket_map = {
        'originals': BUCKET_ORIGINALS,
        'exif': BUCKET_EXIF,
        'processing': BUCKET_PROCESSING,
        'published': BUCKET_PUBLISHED,
    }
    bucket = bucket_map.get(bucket_type, BUCKET_PROCESSING)
    
    if r2_path.startswith('r2://'):
        parts = r2_path.replace('r2://', '').split('/', 1)
        bucket = parts[0]
        key = parts[1] if len(parts) > 1 else parts[0]
    else:
        key = r2_path
    
    return _r2_storage.delete(bucket, key)


def file_exists(r2_path: str, bucket_type: str = 'processing') -> bool:
    """Check if file exists in R2."""
    bucket_map = {
        'originals': BUCKET_ORIGINALS,
        'exif': BUCKET_EXIF,
        'processing': BUCKET_PROCESSING,
        'published': BUCKET_PUBLISHED,
    }
    bucket = bucket_map.get(bucket_type, BUCKET_PROCESSING)
    
    if r2_path.startswith('r2://'):
        parts = r2_path.replace('r2://', '').split('/', 1)
        bucket = parts[0]
        key = parts[1] if len(parts) > 1 else parts[0]
    else:
        key = r2_path
    
    return _r2_storage.exists(bucket, key)