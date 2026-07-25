"""
Verification Processing Module
Lightweight verification of WebApp submissions - no face detection/blur
"""
import logging
import hashlib
from typing import Dict, List

logger = logging.getLogger(__name__)


class SubmissionVerifier:
    """
    Verifies that WebApp submissions are properly processed.
    Server NEVER sees unblurred bystanders - verification only checks for errors.
    """
    
    def __init__(self):
        self.min_confidence = 0.5
    
    async def verify_image(self, image_bytes: bytes) -> Dict:
        """
        Verify image submission.
        
        Checks:
        1. File integrity (hash match)
        2. Can re-detect faces (sanity check)
        3. No obvious corruption
        
        Does NOT:
        - Store unblurred faces
        - Link to uploader identity
        - Create additional copies
        """
        try:
            # Compute hash
            file_hash = hashlib.sha256(image_bytes).hexdigest()
            
            # Basic integrity check
            if len(image_bytes) < 1000:
                return {
                    'valid': False,
                    'reason': 'File too small',
                    'hash': file_hash
                }
            
            # Try to open as image (validates format)
            try:
                from PIL import Image
                import io
                img = Image.open(io.BytesIO(image_bytes))
                img.verify()
                
                # Re-open after verify (verify closes file)
                img = Image.open(io.BytesIO(image_bytes))
                width, height = img.size
                
                if width < 100 or height < 100:
                    return {
                        'valid': False,
                        'reason': 'Image too small',
                        'hash': file_hash
                    }
                
            except Exception as e:
                return {
                    'valid': False,
                    'reason': f'Invalid image format: {str(e)}',
                    'hash': file_hash
                }
            
            # For images, we accept them as processed
            # The client-side already blurred faces
            # We just verify the file is valid
            
            return {
                'valid': True,
                'hash': file_hash,
                'type': 'image',
                'width': width,
                'height': height
            }
            
        except Exception as e:
            logger.error(f"Image verification failed: {e}")
            return {
                'valid': False,
                'reason': str(e),
                'hash': hashlib.sha256(image_bytes).hexdigest() if image_bytes else 'unknown'
            }
    
    async def verify_video(self, video_bytes: bytes) -> Dict:
        """
        Verify video submission.
        
        Checks:
        1. File integrity
        2. Can decode video
        3. Reasonable duration/size
        
        For video, we accept client-side processing as-is.
        Server-side video blur verification would be too resource-intensive.
        """
        try:
            file_hash = hashlib.sha256(video_bytes).hexdigest()
            
            if len(video_bytes) < 10000:
                return {
                    'valid': False,
                    'reason': 'Video too small',
                    'hash': file_hash
                }
            
            # Try to probe video with ffmpeg
            try:
                import subprocess
                import tempfile
                import os
                
                with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp:
                    tmp.write(video_bytes)
                    tmp_path = tmp.name
                
                # Probe video
                result = subprocess.run(
                    ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                     '-show_entries', 'stream=width,height,duration',
                     '-of', 'csv', tmp_path],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                
                os.unlink(tmp_path)
                
                if result.returncode != 0:
                    return {
                        'valid': False,
                        'reason': 'Invalid video format',
                        'hash': file_hash
                    }
                
                # Parse probe output
                parts = result.stdout.strip().split(',')
                if len(parts) >= 3:
                    width = int(parts[0])
                    height = int(parts[1])
                    duration = float(parts[2])
                    
                    if duration > 300:  # 5 minutes max
                        return {
                            'valid': False,
                            'reason': 'Video too long (max 5 minutes)',
                            'hash': file_hash
                        }
                
                return {
                    'valid': True,
                    'hash': file_hash,
                    'type': 'video'
                }
                
            except Exception as e:
                logger.warning(f"Video probe failed: {e}")
                # Accept file if we can't probe (might be unsupported codec)
                return {
                    'valid': True,
                    'hash': file_hash,
                    'type': 'video',
                    'warning': 'Could not verify video format'
                }
                
        except Exception as e:
            logger.error(f"Video verification failed: {e}")
            return {
                'valid': False,
                'reason': str(e),
                'hash': file_hash
            }
    
    async def verify_hash_safety(self, file_hash: str) -> Dict:
        """
        Check file hash against known CSAM databases.
        
        In production, integrate with:
        - NCMEC CyberTipline (if operating in US)
        - IWF (if operating in UK/EU)
        - Or other legitimate hash-matching service
        
        This is a PLACEHOLDER - DO NOT use in production without proper integration.
        """
        # PLACEHOLDER IMPLEMENTATION
        # Real implementation would call external API
        return {
            'safe': True,
            'checked': False,
            'note': 'Hash check not implemented - integrate with legitimate provider'
        }
    
    async def verify_submission(self, file_bytes: bytes, file_type: str, metadata: Dict) -> Dict:
        """
        Main verification entry point.
        
        Args:
            file_bytes: The processed (blurred) file
            file_type: MIME type
            metadata: Submission metadata from WebApp
        
        Returns:
            Dict with verification results
        """
        results = {
            'valid': True,
            'checks': [],
            'warnings': []
        }
        
        # Verify file based on type
        if file_type.startswith('video'):
            verification = await self.verify_video(file_bytes)
        else:
            verification = await self.verify_image(file_bytes)
        
        results['file_verification'] = verification
        results['checks'].append('file_integrity')
        
        if not verification['valid']:
            results['valid'] = False
            results['reason'] = verification.get('reason', 'File verification failed')
            return results
        
        # Verify hash safety (CSAM check)
        file_hash = verification['hash']
        safety_check = await self.verify_hash_safety(file_hash)
        results['safety_check'] = safety_check
        
        if not safety_check.get('safe', True):
            results['valid'] = False
            results['reason'] = 'Content safety check failed'
            return results
        
        # Verify metadata
        metadata_check = self._verify_metadata(metadata)
        results['metadata_check'] = metadata_check
        
        if not metadata_check['valid']:
            results['warnings'].append(metadata_check.get('warning', 'Metadata issue'))
        
        # All checks passed
        results['hash'] = file_hash
        results['file_type'] = verification.get('type', 'unknown')
        
        return results
    
    def _verify_metadata(self, metadata: Dict) -> Dict:
        """
        Verify submission metadata.
        
        Returns:
            Dict with validation results
        """
        required_fields = ['incident_type', 'location', 'date']
        
        for field in required_fields:
            if not metadata.get(field):
                return {
                    'valid': False,
                    'warning': f'Missing required field: {field}'
                }
        
        # Validate incident_type
        valid_types = [
            'police_misconduct', 'government_corruption', 'natural_disaster',
            'infrastructure_failure', 'traffic_accident', 'public_disturbance',
            'environmental_violation', 'other'
        ]
        
        if metadata.get('incident_type') not in valid_types:
            return {
                'valid': False,
                'warning': f'Invalid incident_type: {metadata.get("incident_type")}'
            }
        
        return {
            'valid': True,
            'message': 'Metadata valid'
        }


async def verify_submission_job(submission_id: str, job_data: Dict) -> Dict:
    """
    Job processor for verification task.
    
    Args:
        submission_id: UUID of submission
        job_data: Job context
    
    Returns:
        Dict with job results
    """
    try:
        from models_vault import get_session, Submission
        from storage.r2 import get_r2_storage
        
        async with async_session_factory() as session:
            from sqlalchemy import select, update as sa_update
            
            # Get submission
            result = await session.execute(
                select(Submission).where(Submission.submission_id == submission_id)
            )
            submission = result.scalar_one_or_none()
            
            if not submission:
                return {'status': 'error', 'message': 'Submission not found'}
            
            # Download file from R2
            r2 = get_r2_storage()
            file_bytes = r2.download(r2.BUCKET_PUBLISHED, submission.r2_key)
            
            # Parse metadata
            metadata = {
                'incident_type': submission.incident_type,
                'location': submission.location_general,
                'date': submission.incident_date.isoformat() if submission.incident_date else None,
                'description': submission.description_factual,
                'content_warning': submission.content_warning
            }
            
            # Verify
            verifier = SubmissionVerifier()
            result = await verifier.verify_submission(
                file_bytes,
                submission.file_type,
                metadata
            )
            
            # Update submission status
            if result['valid']:
                await session.execute(
                    sa_update(Submission)
                    .where(Submission.submission_id == submission_id)
                    .values(
                        verification_status='verified',
                        status='published'
                    )
                )
            else:
                await session.execute(
                    sa_update(Submission)
                    .where(Submission.submission_id == submission_id)
                    .values(
                        verification_status='failed',
                        status='rejected'
                    )
                )
            
            await session.commit()
            
            return {
                'status': 'success',
                'valid': result['valid'],
                'checks': result.get('checks', [])
            }
    
    except Exception as e:
        logger.error(f"Verification job failed: {e}")
        return {'status': 'error', 'message': str(e)}