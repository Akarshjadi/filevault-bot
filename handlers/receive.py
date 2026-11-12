"""
Receive Handler - Processes WebApp submissions
Receives already-blurred files from the Mini App
"""
import logging
import json
import hashlib
from datetime import datetime
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from models_vault import get_session
from database import async_session_factory
from storage.r2 import get_r2_storage
from tasks import enqueue_processing_job

logger = logging.getLogger(__name__)


async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle data received from Telegram Mini App
    
    Expected payload:
    {
        "file": "base64_encoded_file",
        "metadata": {
            "incident_type": "police_misconduct",
            "location": "Connaught Place, Delhi",
            "date": "2024-01-15T14:30",
            "description": "Factual description",
            "content_warning": false,
            "official_tag_count": 2,
            "file_hash": "sha256_hex"
        }
    }
    """
    try:
        # Parse WebApp data
        webapp_data = json.loads(update.message.web_app_data.data)
        file_b64 = webapp_data.get('file')
        metadata = webapp_data.get('metadata', {})
        
        if not file_b64:
            await update.message.reply_text("❌ No file received. Please try again.")
            return
        
        # Decode file
        import base64
        file_bytes = base64.b64decode(file_b64)
        file_hash = metadata.get('file_hash', hashlib.sha256(file_bytes).hexdigest())
        
        await update.message.reply_text("🔄 Verifying submission...", parse_mode='Markdown')
        
        # Store in R2 (only published bucket - no originals)
        r2 = get_r2_storage()
        
        # Determine file extension
        file_type = metadata.get('file_type', 'image/jpeg')
        ext = 'mp4' if 'video' in file_type else 'jpg'
        
        # Upload to published bucket ONLY (no originals)
        r2_key = f"{file_hash}/final.{ext}"
        r2.upload_published(file_hash, file_bytes)
        
        # Save to database
        from models_vault import Submission as SubmissionModel, Incident as IncidentModel
        
        async with async_session_factory() as session:
            from sqlalchemy import select, insert
            
            # Create incident record
            incident = IncidentModel(
                incident_type=metadata.get('incident_type', 'other'),
                location_general=metadata.get('location', 'Unknown'),
                incident_date=datetime.utcnow(),
                description_factual=metadata.get('description', ''),
                content_warning=metadata.get('content_warning', False),
            )
            session.add(incident)
            await session.flush()
            
            # Create submission record
            result = await session.execute(
                insert(SubmissionModel)
                .values(
                    original_hash=file_hash,
                    uploader_anonymous_token=generate_rate_limit_token(update.effective_user.id),
                    incident_id=incident.incident_id,
                    file_type=ext,
                    file_size=len(file_bytes),
                    status='pending_review'
                )
                .returning(SubmissionModel.submission_id)
            )
            submission_id = result.scalar_one()
            await session.commit()
        
        # Enqueue verification job
        await enqueue_processing_job(str(submission_id), 'verify_submission')
        
        await update.message.reply_text(
            "✅ **Submission Received**\n\n"
            "Your evidence has been uploaded and is pending automated verification.\n\n"
            f"Submission ID: `{str(submission_id)[:16]}...`\n\n"
            "The system will check:\n"
            "1. Face re-detection (ensure all bystanders blurred)\n"
            "2. Content safety hash check\n"
            "3. Metadata validation\n\n"
            "You'll be notified once verification is complete.",
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"WebApp data handling failed: {e}")
        await update.message.reply_text(
            "❌ **Submission Failed**\n\n"
            "There was an error processing your submission.\n"
            "Please try again or contact support.",
            parse_mode='Markdown'
        )


def generate_rate_limit_token(user_id: int) -> str:
    """
    Generate rotating rate-limit token (not identity-linked)
    Used only for spam prevention, not identity tracking
    """
    import hmac
    import hashlib
    from crypto_utils import MASTER_SALT
    
    token_data = f"rate_limit:{user_id}:{datetime.utcnow().date()}".encode()
    return hmac.new(MASTER_SALT, token_data, hashlib.sha256).hexdigest()


def get_receive_handlers():
    """Return list of handlers for WebApp data."""
    from telegram.ext import WebAppQueryHandler
    return [
        # WebApp data handler will be added in bot.py
    ]