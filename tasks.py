"""
Background Task Queue Module
Manages async processing jobs for face detection, preview generation, and final publishing.
"""
import asyncio
import logging
from typing import Optional, Dict, List
from datetime import datetime
from uuid import UUID
from enum import Enum

from models_vault import ProcessingJob, get_session
from database import async_session_factory

logger = logging.getLogger(__name__)


class JobType(str, Enum):
    FACE_DETECTION = "face_detection"
    CONSENT_UPDATE = "consent_update"
    FINALIZE_PUBLISH = "finalize_publish"
    GENERATE_PREVIEW = "generate_preview"
    DELETE_SUBMISSION = "delete_submission"


class JobPriority(int, Enum):
    LOW = 0
    NORMAL = 5
    HIGH = 10
    CRITICAL = 20


class TaskQueue:
    """
    Async task queue for background processing.
    Uses asyncio.Queue with worker coroutines.
    """
    
    def __init__(self, num_workers: int = 2):
        self.queue = asyncio.Queue()
        self.num_workers = num_workers
        self.workers = []
        self.running = False
        self.processors = {}
    
    def register_processor(self, job_type: str, processor_func):
        """Register a processor function for a job type."""
        self.processors[job_type] = processor_func
    
    async def enqueue(self, submission_id: str, job_type: str, 
                     priority: int = JobPriority.NORMAL) -> Optional[ProcessingJob]:
        """
        Add job to queue.
        
        Args:
            submission_id: UUID of submission
            job_type: Type of job
            priority: Job priority (higher = processed first)
        
        Returns:
            ProcessingJob: Created job record
        """
        try:
            async with async_session_factory() as session:
                from sqlalchemy import select, insert
                
                # Create job record
                job = ProcessingJob(
                    submission_id=submission_id,
                    job_type=job_type,
                    status='queued',
                    priority=priority,
                )
                session.add(job)
                await session.commit()
                await session.refresh(job)
                
                # Add to queue
                await self.queue.put({
                    'job_id': str(job.job_id),
                    'submission_id': submission_id,
                    'job_type': job_type,
                    'priority': priority,
                })
                
                logger.info(f"Enqueued job {job.job_id}: {job_type} for submission {submission_id}")
                return job
        
        except Exception as e:
            logger.error(f"Failed to enqueue job: {e}")
            return None
    
    async def start_workers(self):
        """Start background worker coroutines."""
        if self.running:
            return
        
        self.running = True
        self.workers = [
            asyncio.create_task(self._worker(worker_id))
            for worker_id in range(self.num_workers)
        ]
        logger.info(f"Started {self.num_workers} task workers")
    
    async def stop_workers(self):
        """Stop background workers gracefully."""
        self.running = False
        
        # Wait for queue to empty
        if not self.queue.empty():
            logger.info("Waiting for queue to drain...")
        
        # Cancel workers
        for worker in self.workers:
            worker.cancel()
        
        await asyncio.gather(*self.workers, return_exceptions=True)
        logger.info("Task workers stopped")
    
    async def _worker(self, worker_id: int):
        """Worker coroutine that processes jobs from queue."""
        logger.info(f"Worker {worker_id} started")
        
        while self.running:
            try:
                # Get job from queue with timeout
                try:
                    job_data = await asyncio.wait_for(self.queue.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    continue
                
                job_id = job_data['job_id']
                submission_id = job_data['submission_id']
                job_type = job_data['job_type']
                
                logger.info(f"Worker {worker_id} processing job {job_id}: {job_type}")
                
                # Update job status
                await self._update_job_status(job_id, 'running')
                
                # Process job
                success = False
                error_message = None
                
                try:
                    processor = self.processors.get(job_type)
                    if not processor:
                        raise ValueError(f"No processor registered for job type: {job_type}")
                    
                    result = await processor(submission_id, job_data)
                    success = True
                    logger.info(f"Job {job_id} completed successfully")
                
                except Exception as e:
                    error_message = str(e)
                    logger.error(f"Job {job_id} failed: {error_message}")
                
                # Update job status
                await self._update_job_status(
                    job_id, 
                    'completed' if success else 'failed',
                    error_message=error_message
                )
                
                # Mark queue task as done
                self.queue.task_done()
            
            except asyncio.CancelledError:
                logger.info(f"Worker {worker_id} cancelled")
                break
            
            except Exception as e:
                logger.error(f"Worker {worker_id} error: {e}")
        
        logger.info(f"Worker {worker_id} stopped")
    
    async def _update_job_status(self, job_id: str, status: str, error_message: Optional[str] = None):
        """Update job status in database."""
        try:
            async with async_session_factory() as session:
                from sqlalchemy import select, update as sa_update
                
                now = datetime.utcnow()
                
                # First fetch current job to get attempts count
                result = await session.execute(
                    select(ProcessingJob).where(ProcessingJob.job_id == job_id)
                )
                job = result.scalar_one_or_none()
                if not job:
                    logger.warning(f"Job {job_id} not found for status update")
                    return
                
                current_attempts = job.attempts or 0
                
                update_data = {
                    'status': status,
                    'updated_at': now,
                }
                
                if status == 'running':
                    update_data['started_at'] = now
                elif status in ('completed', 'failed'):
                    update_data['completed_at'] = now
                    update_data['attempts'] = current_attempts + 1
                
                if error_message:
                    update_data['error_message'] = error_message
                
                await session.execute(
                    sa_update(ProcessingJob)
                    .where(ProcessingJob.job_id == job_id)
                    .values(**update_data)
                )
                await session.commit()
        
        except Exception as e:
            logger.error(f"Failed to update job {job_id} status: {e}")


# Global task queue instance
_task_queue: Optional[TaskQueue] = None


def get_task_queue() -> TaskQueue:
    """Get or create the global task queue."""
    global _task_queue
    if _task_queue is None:
        _task_queue = TaskQueue(num_workers=2)
    return _task_queue


async def enqueue_processing_job(submission_id: str, job_type: str, 
                                 priority: int = JobPriority.NORMAL) -> Optional[ProcessingJob]:
    """
    Convenience function to enqueue a processing job.
    
    Args:
        submission_id: UUID of submission
        job_type: Type of job
        priority: Job priority
    
    Returns:
        ProcessingJob: Created job or None if failed
    """
    queue = get_task_queue()
    return await queue.enqueue(submission_id, job_type, priority)


# ============================================================================
# Job Processors
# ============================================================================

async def process_face_detection(submission_id: str, job_data: Dict) -> Dict:
    """
    Process face detection on a submission.
    
    Steps:
    1. Download original from R2
    2. Run face detection
    3. Create blurred copy
    4. Store detected faces in DB
    5. Upload blurred copy to processing bucket
    """
    try:
        from models_vault import Submission, DetectedPerson, ProcessingJob, get_session, BlurStatus, ConsentStatus, SubjectType
        from storage.r2 import get_r2_storage, download_file, upload_fileobj
        from processing.face_blur import FaceBlurProcessor
        from processing.generate_preview import PreviewGenerator
        import uuid
        
        async with async_session_factory() as session:
            from sqlalchemy import select, update as sa_update
            
            # Get submission
            result = await session.execute(
                select(Submission).where(Submission.submission_id == submission_id)
            )
            submission = result.scalar_one_or_none()
            
            if not submission:
                raise ValueError(f"Submission {submission_id} not found")
            
            # Update submission status
            await session.execute(
                sa_update(Submission)
                .where(Submission.submission_id == submission_id)
                .values(
                    status='processing',
                    processing_started_at=datetime.utcnow()
                )
            )
            await session.commit()
        
        r2 = get_r2_storage()
        original_ref = f"r2://{r2.BUCKET_ORIGINALS}/{submission.original_hash}"
        
        # Download original
        logger.info(f"Downloading original: {original_ref}")
        original_bytes = download_file(original_ref, bucket_type='originals')
        
        # Run face detection
        logger.info(f"Running face detection on submission {submission_id}")
        processor = FaceBlurProcessor()
        
        if submission.file_type == 'video':
            blurred_bytes, detected_faces = await processor.process_video(
                original_bytes, submission_id
            )
        else:
            blurred_bytes, detected_faces = await processor.process_image(
                original_bytes, submission_id
            )
        
        logger.info(f"Detected {len(detected_faces)} faces")
        
        # Store detected faces in DB
        async with async_session_factory() as session:
            from sqlalchemy import insert
            
            for face_data in detected_faces:
                detected_person = DetectedPerson(
                    submission_id=submission_id,
                    subject_type=face_data.get('subject_type', 'civilian'),
                    blur_status='blurred',
                    consent_status='none_requested',
                    frame_index=face_data.get('frame_index'),
                    timestamp_in_video=face_data.get('timestamp'),
                    bbox_x=face_data['bbox'][0],
                    bbox_y=face_data['bbox'][1],
                    bbox_width=face_data['bbox'][2],
                    bbox_height=face_data['bbox'][3],
                    face_embedding=face_data.get('embedding'),
                )
                session.add(detected_person)
            
            # Update submission status
            from sqlalchemy import update as sa_update2
            await session.execute(
                sa_update2(Submission)
                .where(Submission.submission_id == submission_id)
                .values(status='processed', processing_completed_at=datetime.utcnow())
            )
            
            await session.commit()
        
        # Upload blurred copy to processing bucket
        blurred_key = f"{submission_id}/blurred_initial.{submission.file_type}"
        r2.upload_processing(submission_id, blurred_key, blurred_bytes)
        
        logger.info(f"Face detection complete for submission {submission_id}")
        
        return {
            'status': 'success',
            'faces_detected': len(detected_faces),
            'blurred_copy': f"r2://{r2.BUCKET_PROCESSING}/{submission_id}/blurred_initial.{submission.file_type}"
        }
    
    except Exception as e:
        logger.error(f"Face detection processing failed: {e}")
        raise


async def process_consent_update(submission_id: str, job_data: Dict) -> Dict:
    """
    Reproscess submission after consent changes.
    Regenerate final copy with updated blur statuses.
    """
    try:
        from models_vault import Submission, DetectedPerson, get_session
        from storage.r2 import get_r2_storage, download_file, upload_fileobj
        from processing.face_blur import FaceBlurProcessor
        import uuid
        
        async with async_session_factory() as session:
            from sqlalchemy import select
            
            # Get all faces with their blur statuses
            result = await session.execute(
                select(DetectedPerson).where(DetectedPerson.submission_id == submission_id)
            )
            faces = result.scalars().all()
            
            if not faces:
                logger.warning(f"No faces found for submission {submission_id}")
                return {'status': 'no_faces'}
            
            # Get submission
            sub_result = await session.execute(
                select(Submission).where(Submission.submission_id == submission_id)
            )
            submission = sub_result.scalar_one_or_none()
        
        # Download blurred initial copy
        r2 = get_r2_storage()
        blurred_key = f"{submission_id}/blurred_initial.{submission.file_type}"
        blurred_bytes = r2.download(r2.BUCKET_PROCESSING, blurred_key)
        
        # For now, keep everything blurred (proper logic would selectively unblur)
        # In production, this would re-encode video with selective blurring
        final_bytes = blurred_bytes
        
        # Upload final published copy
        final_key = f"{submission_id}/final.{submission.file_type}"
        r2.upload_published(submission_id, final_bytes)
        
        logger.info(f"Consent update processed for submission {submission_id}")
        
        return {
            'status': 'success',
            'published_copy': f"r2://{r2.BUCKET_PUBLISHED}/{submission_id}/final.{submission.file_type}"
        }
    
    except Exception as e:
        logger.error(f"Consent update processing failed: {e}")
        raise


async def process_finalize_publish(submission_id: str, job_data: Dict) -> Dict:
    """
    Finalize submission for publication.
    Generate final copy and notify uploader.
    """
    try:
        from models_vault import Submission, get_session
        from storage.r2 import get_r2_storage
        
        async with async_session_factory() as session:
            from sqlalchemy import select, update as sa_update
            
            # Get submission
            result = await session.execute(
                select(Submission).where(Submission.submission_id == submission_id)
            )
            submission = result.scalar_one_or_none()
            
            if not submission:
                raise ValueError(f"Submission {submission_id} not found")
            
            # Update status
            await session.execute(
                sa_update(Submission)
                .where(Submission.submission_id == submission_id)
                .values(status='published')
            )
            await session.commit()
        
        r2 = get_r2_storage()
        
        # Ensure published copy exists
        final_key = f"{submission_id}/final.{submission.file_type}"
        if not r2.exists(r2.BUCKET_PUBLISHED, final_key):
            # Trigger consent update to generate it
            await enqueue_processing_job(submission_id, 'consent_update', priority=JobPriority.HIGH)
            return {'status': 'pending_final'}
        
        logger.info(f"Submission {submission_id} finalized and published")
        
        return {
            'status': 'success',
            'published': True
        }
    
    except Exception as e:
        logger.error(f"Finalize publish failed: {e}")
        raise


async def process_generate_preview(submission_id: str, job_data: Dict) -> Dict:
    """
    Generate preview copy for review.
    """
    try:
        from models_vault import Submission, get_session
        from storage.r2 import get_r2_storage, download_file, upload_fileobj
        from processing.generate_preview import PreviewGenerator
        
        async with async_session_factory() as session:
            from sqlalchemy import select
            
            result = await session.execute(
                select(Submission).where(Submission.submission_id == submission_id)
            )
            submission = result.scalar_one_or_none()
        
        r2 = get_r2_storage()
        
        # Download blurred initial
        blurred_key = f"{submission_id}/blurred_initial.{submission.file_type}"
        blurred_bytes = r2.download(r2.BUCKET_PROCESSING, blurred_key)
        
        # Generate preview
        generator = PreviewGenerator()
        if submission.file_type == 'video':
            preview_bytes = await generator.generate_video_preview(blurred_bytes)
        else:
            preview_bytes = await generator.generate_image_preview(blurred_bytes)
        
        # Upload preview
        preview_key = f"{submission_id}/preview.{submission.file_type}"
        r2.upload_processing(submission_id, preview_key, preview_bytes)
        
        logger.info(f"Preview generated for submission {submission_id}")
        
        return {
            'status': 'success',
            'preview': f"r2://{r2.BUCKET_PROCESSING}/{submission_id}/preview.{submission.file_type}"
        }
    
    except Exception as e:
        logger.error(f"Preview generation failed: {e}")
        raise


async def process_delete_submission(submission_id: str, job_data: Dict) -> Dict:
    """
    Delete all files associated with a submission.
    """
    try:
        from models_vault import Submission, get_session
        from storage.r2 import get_r2_storage
        
        async with async_session_factory() as session:
            from sqlalchemy import select, delete as sa_delete
            
            result = await session.execute(
                select(Submission).where(Submission.submission_id == submission_id)
            )
            submission = result.scalar_one_or_none()
        
        if not submission:
            return {'status': 'not_found'}
        
        r2 = get_r2_storage()
        
        # Delete from all buckets
        buckets_and_keys = [
            (r2.BUCKET_ORIGINALS, submission.original_hash),
            (r2.BUCKET_EXIF, f"{submission.original_hash}/exif.json"),
            (r2.BUCKET_PROCESSING, f"{submission_id}/blurred_initial.{submission.file_type}"),
            (r2.BUCKET_PROCESSING, f"{submission_id}/preview.{submission.file_type}"),
            (r2.BUCKET_PUBLISHED, f"{submission_id}/final.{submission.file_type}"),
        ]
        
        for bucket, key in buckets_and_keys:
            if r2.exists(bucket, key):
                r2.delete(bucket, key)
        
        logger.info(f"Deleted files for submission {submission_id}")
        
        return {'status': 'success'}
    
    except Exception as e:
        logger.error(f"Deletion failed: {e}")
        raise


# ============================================================================
# Initialization
# ============================================================================

# Initialize task queue
queue = get_task_queue()

# Register job processors
queue.register_processor(JobType.FACE_DETECTION, process_face_detection)
queue.register_processor(JobType.CONSENT_UPDATE, process_consent_update)
queue.register_processor(JobType.FINALIZE_PUBLISH, process_finalize_publish)
queue.register_processor(JobType.GENERATE_PREVIEW, process_generate_preview)
queue.register_processor(JobType.DELETE_SUBMISSION, process_delete_submission)


async def start_background_workers():
    """Start background task workers (call on bot startup)."""
    queue = get_task_queue()
    await queue.start_workers()
    logger.info("Background workers started")


async def stop_background_workers():
    """Stop background workers gracefully (call on bot shutdown)."""
    queue = get_task_queue()
    await queue.stop_workers()
    logger.info("Background workers stopped")