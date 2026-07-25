"""
Submission Handler
Manages the evidence submission flow: file upload, incident selection, and review.
"""
import os
import io
import hashlib
import logging
from typing import Optional, Dict, List
from datetime import datetime

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, CallbackQueryHandler, MessageHandler, filters

from models_vault import Profile, Incident, Submission, DetectedPerson, ConsentStatus, BlurStatus, SubjectType, get_session
from crypto_utils import hash_telegram_user_id, generate_anonymous_token
from database import async_session_factory
from storage.r2 import get_r2_storage
from processing.face_blur import FaceBlurProcessor
from processing.generate_preview import PreviewGenerator
from tasks import enqueue_processing_job

logger = logging.getLogger(__name__)

# Conversation states
(
    SUBMIT_AWAITING_FILE,
    SUBMIT_INCIDENT_TYPE,
    SUBMIT_LOCATION,
    SUBMIT_DATE,
    SUBMIT_DESCRIPTION,
    SUBMIT_CONTENT_WARNING,
    SUBMIT_AWAITING_REVIEW,
) = range(7)


class SubmissionData:
    """Helper class to store submission data during conversation."""
    def __init__(self):
        self.file_bytes: Optional[bytes] = None
        self.filename: str = ""
        self.file_type: str = ""
        self.file_hash: str = ""
        self.exif_data: Dict = {}
        self.incident_id: Optional[str] = None
        self.incident_type: str = ""
        self.location: str = ""
        self.incident_date: Optional[datetime] = None
        self.description: str = ""
        self.content_warning: bool = False
        self.profile_id: Optional[str] = None


async def submit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start submission process."""
    user = update.effective_user
    telegram_id_hash = hash_telegram_user_id(user.id)
    
    # Verify registration (optional for anonymous submissions)
    # For maximum anonymity, we allow submissions without registration
    # Registration only needed for consent management
    
    # Initialize submission data
    context.user_data['submission'] = SubmissionData()
    context.user_data['telegram_id_hash'] = telegram_id_hash
    
    await update.message.reply_text(
        "📤 **Submit Evidence**\n\n"
        "Please send the **photo or video file** you want to submit.\n\n"
        "⚠️ **Important Notes:**\n"
        "• Original files will be stored securely in Cloudflare R2\n"
        "• All faces will be blurred by default\n"
        "• EXIF data will be extracted and stored separately\n"
        "• Large files may take time to process\n\n"
        "Send your file now:",
        parse_mode='Markdown'
    )
    
    return SUBMIT_AWAITING_FILE


async def handle_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle uploaded photo or video."""
    # Determine file info
    if update.message.photo:
        # Photo - get largest size
        photo = update.message.photo[-1]
        file_obj = await photo.get_file()
        file_type = "image"
        filename = f"photo_{datetime.utcnow().timestamp()}.jpg"
    elif update.message.video:
        file_obj = await update.message.video.get_file()
        file_type = "video"
        filename = update.message.video.file_name or f"video_{datetime.utcnow().timestamp()}.mp4"
    elif update.message.document:
        file_obj = await update.message.document.get_file()
        file_type = "document"
        filename = update.message.document.file_name or f"file_{datetime.utcnow().timestamp()}"
    else:
        await update.message.reply_text(
            "❌ Unsupported file type.\nPlease send a photo or video."
        )
        return SUBMIT_AWAITING_FILE
    
    # Download file
    await update.message.reply_text("⬇️ Downloading file...")
    file_bytes = await file_obj.download_as_bytearray()
    file_bytes = bytes(file_bytes)
    
    # Compute hash
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    
    # STRIP EXIF for privacy BEFORE storing
    if file_type == "image":
        from processing.face_blur import FaceBlurProcessor
        processor = FaceBlurProcessor()
        try:
            # Strip all EXIF to protect uploader location/privacy
            file_bytes = processor.strip_exif(file_bytes, filename)
            # Extract EXIF for logging (separate from original)
            exif_data = processor.extract_exif(file_bytes, filename)
        except Exception as e:
            logger.warning(f"EXIF processing failed: {e}")
            exif_data = {}
    
    # Store in session
    submission = context.user_data['submission']
    submission.file_bytes = file_bytes
    submission.filename = filename
    submission.file_type = file_type
    submission.file_hash = file_hash
    submission.exif_data = exif_data
    
    # Upload to R2 originals immediately
    r2 = get_r2_storage()
    try:
        await update.message.reply_text("☁️ Uploading to secure storage...")
        r2_uri = r2.upload_original(file_bytes, file_hash, filename)
        
        # Upload EXIF if present
        if exif_data:
            import json
            exif_bytes = json.dumps(exif_data).encode()
            r2.upload_exif(file_hash, exif_bytes)
        
        await update.message.reply_text(
            f"✅ File uploaded securely.\n"
            f"Hash: `{file_hash[:16]}...`\n"
            f"Size: {len(file_bytes) / 1024 / 1024:.2f} MB",
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"R2 upload failed: {e}")
        await update.message.reply_text(
            "❌ Upload to secure storage failed.\nPlease try again."
        )
        return SUBMIT_AWAITING_FILE
    
    # Ask for incident details
    await ask_incident_type(update, context)
    return SUBMIT_INCIDENT_TYPE


async def ask_incident_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask user for incident type."""
    keyboard = [
        [InlineKeyboardButton("🚔 Police Misconduct", callback_data="type_police"),
         InlineKeyboardButton("🏛️ Government Corruption", callback_data="type_corruption")],
        [InlineKeyboardButton("🌊 Natural Disaster", callback_data="type_disaster"),
         InlineKeyboardButton("🏗️ Infrastructure Failure", callback_data="type_infrastructure")],
        [InlineKeyboardButton("🚗 Traffic Accident", callback_data="type_accident"),
         InlineKeyboardButton("👥 Public Disturbance", callback_data="type_disturbance")],
        [InlineKeyboardButton("🌿 Environmental Violation", callback_data="type_environmental"),
         InlineKeyboardButton("📝 Other", callback_data="type_other")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📂 **Incident Classification**\n\n"
        "What type of incident are you documenting?\n"
        "Select the closest category:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def handle_incident_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle incident type selection."""
    query = update.callback_query
    await query.answer()
    
    incident_type_map = {
        'type_police': 'police_misconduct',
        'type_corruption': 'government_corruption',
        'type_disaster': 'natural_disaster',
        'type_infrastructure': 'infrastructure_failure',
        'type_accident': 'traffic_accident',
        'type_disturbance': 'public_disturbance',
        'type_environmental': 'environmental_violation',
        'type_other': 'other',
    }
    
    submission = context.user_data['submission']
    submission.incident_type = incident_type_map.get(query.data, 'other')
    
    await query.edit_message_text(
        f"✅ Selected: {submission.incident_type.replace('_', ' ').title()}\n\n"
        f"📍 **Location**\n\n"
        f"Please provide the **general location** where the incident occurred.\n"
        f"(e.g., 'Connaught Place, New Delhi' or 'MG Road, Bangalore')\n\n"
        f"⚠️ Do not include specific addresses for privacy.",
        parse_mode='Markdown'
    )
    
    return SUBMIT_LOCATION


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle location input."""
    submission = context.user_data['submission']
    submission.location = update.message.text
    
    await update.message.reply_text(
        "📅 **Incident Date & Time**\n\n"
        "When did the incident occur?\n\n"
        "Send date/time in format: YYYY-MM-DD HH:MM\n"
        "Example: 2024-01-15 14:30\n\n"
        "If unknown, type 'unknown'",
        parse_mode='Markdown'
    )
    
    return SUBMIT_DATE


async def handle_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle incident date input."""
    text = update.message.text.strip()
    submission = context.user_data['submission']
    
    if text.lower() == 'unknown':
        submission.incident_date = datetime.utcnow()
    else:
        try:
            submission.incident_date = datetime.strptime(text, "%Y-%m-%d %H:%M")
        except ValueError:
            await update.message.reply_text(
                "❌ Invalid format. Please use: YYYY-MM-DD HH:MM\n"
                "Example: 2024-01-15 14:30"
            )
            return SUBMIT_DATE
    
    await update.message.reply_text(
        "📝 **Factual Description**\n\n"
        "Please provide a **concise, factual description** of the incident.\n\n"
        "✅ Include:\n"
        "• What happened (objective facts only)\n"
        "• Who was involved\n"
        "• Sequence of events\n\n"
        "❌ Avoid:\n"
        "• Opinions or speculation\n"
        "• Emotional language\n"
        "• Unverified claims\n\n"
        "Example: 'At approximately 2:30 PM, a police vehicle ran a red light at the intersection of MG Road and 5th Cross, colliding with a motorcycle.'",
        parse_mode='Markdown'
    )
    
    return SUBMIT_DESCRIPTION


async def handle_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle description input."""
    submission = context.user_data['submission']
    submission.description = update.message.text
    
    keyboard = [
        [InlineKeyboardButton("⚠️ Yes - Sensitive Content", callback_data="warning_yes"),
         InlineKeyboardButton("✅ No - Safe Content", callback_data="warning_no")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🔞 **Content Warning**\n\n"
        "Does this submission contain sensitive or graphic content?\n"
        "(e.g., violence, injury, explicit material)\n\n"
        "This helps protect viewers and comply with content regulations.",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    
    return SUBMIT_CONTENT_WARNING


async def handle_content_warning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle content warning response."""
    query = update.callback_query
    await query.answer()
    
    submission = context.user_data['submission']
    submission.content_warning = query.data == "warning_yes"
    
    # Create incident (anonymously - no uploader linkage)
    async with async_session_factory() as session:
        incident = Incident(
            incident_type=submission.incident_type,
            location_general=submission.location,
            incident_date=submission.incident_date,
            description_factual=submission.description,
            content_warning=submission.content_warning,
            created_by=None,  # No persistent uploader identity
        )
        session.add(incident)
        await session.commit()
        await session.refresh(incident)
        
        submission.incident_id = str(incident.incident_id)
    
    # Generate anonymous token for this submission (one-way, cannot be reversed)
    anonymous_token = generate_anonymous_token(submission.file_hash, context.user_data['telegram_id_hash'])
    
    # Save submission to database (NO uploader_id - anonymous)
    async with async_session_factory() as session:
        db_submission = Submission(
            original_hash=submission.file_hash,
            uploader_anonymous_token=anonymous_token,
            incident_id=incident.incident_id,
            exif_ref=f"r2://{get_r2_storage().BUCKET_EXIF}/{submission.file_hash}/exif.json",
            file_type=submission.file_type,
            file_size=len(submission.file_bytes),
            status='pending_review',
        )
        session.add(db_submission)
        await session.commit()
        await session.refresh(db_submission)
        
        context.user_data['submission_id'] = str(db_submission.submission_id)
        context.user_data['anonymous_token'] = anonymous_token
    
    # Enqueue background processing job
    await enqueue_processing_job(str(db_submission.submission_id), 'face_detection')
    
    keyboard = [
        [InlineKeyboardButton("📊 View Status", callback_data="status_check")],
        [InlineKeyboardButton("❌ Cancel Submission", callback_data="cancel_submission")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "✅ **Submission Created!**\n\n"
        "🔒 **Anonymous Submission**\n"
        "Your identity is not linked to this submission.\n\n"
        f"Incident ID: `{submission.incident_id[:8]}...`\n"
        f"Submission ID: `{str(db_submission.submission_id)[:8]}...`\n\n"
        "**What happens next:**\n"
        "1. Face detection will run on your submission\n"
        "2. All faces will be blurred by default\n"
        "3. You'll receive a preview for review\n"
        "4. Admin review for publication\n\n"
        "⚠️ **Save your Submission ID** to check status later.\n"
        "You'll be notified when processing is complete.",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    
    return SUBMIT_AWAITING_REVIEW


async def cancel_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel submission."""
    query = update.callback_query
    await query.answer()
    
    # TODO: Clean up R2 files if needed
    
    await query.edit_message_text(
        "❌ Submission cancelled.\nUse /submit to start a new submission."
    )
    return ConversationHandler.END


async def check_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Check submission status."""
    query = update.callback_query
    await query.answer()
    
    submission_id = context.user_data.get('submission_id')
    if not submission_id:
        await query.edit_message_text("No submission found.")
        return ConversationHandler.END
    
    async with async_session_factory() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(Submission).where(Submission.submission_id == submission_id)
        )
        submission = result.scalar_one_or_none()
        
        if not submission:
            await query.edit_message_text("Submission not found.")
            return ConversationHandler.END
        
        status_msg = f"📊 **Submission Status**\n\n"
        status_msg += f"Status: {submission.status}\n"
        status_msg += f"File: {submission.file_type}\n"
        status_msg += f"Submitted: {submission.created_at.strftime('%Y-%m-%d %H:%M')}\n"
        
        if submission.processing_completed_at:
            status_msg += f"Processed: {submission.processing_completed_at.strftime('%Y-%m-%d %H:%M')}\n"
        
        if submission.published_at:
            status_msg += f"Published: {submission.published_at.strftime('%Y-%m-%d %H:%M')}\n"
    
    keyboard = [
        [InlineKeyboardButton("🔄 Refresh", callback_data="status_check")],
        [InlineKeyboardButton("❌ Close", callback_data="close_status")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(status_msg, reply_markup=reply_markup, parse_mode='Markdown')
    return SUBMIT_AWAITING_REVIEW


async def close_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Close status message."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Use /status to check your submission again.")
    return ConversationHandler.END


def get_submission_handler() -> ConversationHandler:
    """Return the submission conversation handler."""
    return ConversationHandler(
        entry_points=[CommandHandler("submit", submit_start)],
        states={
            SUBMIT_AWAITING_FILE: [
                MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_file_upload)
            ],
            SUBMIT_INCIDENT_TYPE: [
                CallbackQueryHandler(handle_incident_type, pattern="^type_")
            ],
            SUBMIT_LOCATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_location)
            ],
            SUBMIT_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_date)
            ],
            SUBMIT_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_description)
            ],
            SUBMIT_CONTENT_WARNING: [
                CallbackQueryHandler(handle_content_warning, pattern="^warning_")
            ],
            SUBMIT_AWAITING_REVIEW: [
                CallbackQueryHandler(check_status, pattern="^status_check$"),
                CallbackQueryHandler(close_status, pattern="^close_status$"),
                CallbackQueryHandler(cancel_submission, pattern="^cancel_submission$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_submission)],
        per_user=True,
        per_chat=True,
    )