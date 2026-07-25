"""
Consent Handler
Manages face tagging, consent requests, and consent resolution.
"""
import logging
from typing import Optional, Dict
from datetime import datetime

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from models_vault import (
    Profile, Submission, DetectedPerson, ConsentStatus, BlurStatus, SubjectType,
    ConsentLog, FaceEmbedding, get_session
)
from crypto_utils import hash_telegram_user_id
from database import async_session_factory
from storage.r2 import get_r2_storage, get_presigned_url
from processing.generate_preview import PreviewGenerator

logger = logging.getLogger(__name__)

# Consent version tracking
CURRENT_CONSENT_VERSION = "1.0"


async def request_selfie_verification(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                                     face_id: str, submission_id: str) -> int:
    """
    Request selfie from uploader to verify identity for face unblur.
    
    Flow:
    1. Ask user to upload a clear selfie
    2. Compare with detected face embedding
    3. If match, request consent for unblurring
    """
    context.user_data['verification_face_id'] = face_id
    context.user_data['verification_submission_id'] = submission_id
    
    await update.message.reply_text(
        "🔐 **Identity Verification Required**\n\n"
        "To unblur your face, we need to verify your identity.\n\n"
        "Please send a **clear selfie photo** showing your face.\n"
        "Make sure:\n"
        "• Face is clearly visible\n"
        "• Good lighting\n"
        "• No sunglasses or obstructions\n\n"
        "This will be compared with the detected face in the submission.",
        parse_mode='Markdown'
    )
    
    return 1  # Return state for selfie verification


async def handle_selfie_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    """Handle selfie upload for identity verification."""
    if not update.message.photo:
        await update.message.reply_text("❌ Please send a photo.")
        return None
    
    face_id = context.user_data.get('verification_face_id')
    submission_id = context.user_data.get('verification_submission_id')
    
    if not face_id or not submission_id:
        await update.message.reply_text("❌ Session expired. Please restart the process.")
        return None
    
    # Download selfie
    selfie_photo = update.message.photo[-1]
    selfie_file = await selfie_photo.get_file()
    selfie_bytes = await selfie_file.download_as_bytearray()
    selfie_bytes = bytes(selfie_bytes)
    
    await update.message.reply_text("🔄 Verifying identity...")
    
    try:
        from processing.face_blur import FaceBlurProcessor, compute_face_embedding, compare_faces
        
        # Get detected face embedding from DB
        async with async_session_factory() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(DetectedPerson).where(DetectedPerson.face_id == face_id)
            )
            detected_person = result.scalar_one_or_none()
            
            if not detected_person or not detected_person.face_embedding:
                await update.message.reply_text(
                    "❌ Face data not found. Please contact admin."
                )
                return None
            
            stored_embedding = detected_person.face_embedding
        
        # Compute selfie embedding
        import numpy as np
        from PIL import Image
        import cv2
        
        selfie_array = np.array(Image.open(io.BytesIO(selfie_bytes)))
        selfie_array = cv2.cvtColor(selfie_array, cv2.COLOR_RGB2BGR)
        
        # Detect face in selfie
        processor = FaceBlurProcessor()
        _, selfie_faces = await processor.process_image(selfie_bytes, "selfie_verification")
        
        if not selfie_faces:
            await update.message.reply_text(
                "❌ No face detected in selfie. Please send a clearer photo."
            )
            return None
        
        # Get largest face (assumed to be the user)
        selfie_face = max(selfie_faces, key=lambda f: f['bbox'][2] * f['bbox'][3])
        x, y, w, h = selfie_face['bbox']
        face_crop = selfie_array[y:y+h, x:x+w]
        
        # Compute embedding
        selfie_embedding = compute_face_embedding(face_crop)
        if selfie_embedding is None:
            await update.message.reply_text(
                "❌ Could not process selfie. Please try again."
            )
            return None
        
        # Compare embeddings
        is_match = compare_faces(selfie_embedding, np.frombuffer(stored_embedding, dtype=np.float32))
        
        if is_match:
            # Identity verified - ask for consent
            context.user_data['verified_face_id'] = face_id
            context.user_data['verified_submission_id'] = submission_id
            
            keyboard = [
                [InlineKeyboardButton("✅ Yes, unblur my face", callback_data="consent_grant_self"),
                 InlineKeyboardButton("❌ No, keep blurred", callback_data="consent_deny_self")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                "✅ **Identity Verified!**\n\n"
                "Your selfie matches the detected face in the submission.\n\n"
                "Do you want to **unblur your face** in this submission?\n\n"
                "⚠️ **Important:**\n"
                "• This action will be logged with your consent\n"
                "• You can revoke consent later using /forget\n"
                "• This override only applies to this submission\n"
                "• Minors cannot unblur their faces (legal requirement)",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            return 2  # Consent state
        else:
            await update.message.reply_text(
                "❌ **Verification Failed**\n\n"
                "The selfie does not match the detected face.\n"
                "Please ensure you are the person in the submission.\n\n"
                "Try again or contact admin if you believe this is an error."
            )
            return None
    
    except Exception as e:
        logger.error(f"Selfie verification failed: {e}")
        await update.message.reply_text(
            "❌ Verification failed due to an error. Please try again later."
        )
        return None


async def handle_self_consent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle self-consent for face unblur."""
    query = update.callback_query
    await query.answer()
    
    face_id = context.user_data.get('verified_face_id')
    submission_id = context.user_data.get('verified_submission_id')
    user_id = update.effective_user.id
    telegram_id_hash = hash_telegram_user_id(user_id)
    
    if not face_id or not submission_id:
        await query.edit_message_text("❌ Session expired. Please restart.")
        return ConversationHandler.END
    
    granted = query.data == "consent_grant_self"
    
    try:
        async with async_session_factory() as session:
            from sqlalchemy import select, update as sa_update
            
            # Get detected person
            result = await session.execute(
                select(DetectedPerson).where(DetectedPerson.face_id == face_id)
            )
            detected_person = result.scalar_one_or_none()
            
            if not detected_person:
                await query.edit_message_text("❌ Face record not found.")
                return ConversationHandler.END
            
            # Update consent status
            now = datetime.utcnow()
            await session.execute(
                sa_update(DetectedPerson)
                .where(DetectedPerson.face_id == face_id)
                .values(
                    blur_status='unblurred' if granted else 'blurred',
                    consent_status='granted' if granted else 'denied',
                    consent_resolved_at=now
                )
            )
            
            # Log consent action
            consent_log = ConsentLog(
                person_telegram_id_hash=telegram_id_hash,
                submission_id=submission_id,
                face_id=face_id,
                action='granted' if granted else 'denied',
                timestamp=now,
                consent_version=CURRENT_CONSENT_VERSION,
                details={'method': 'selfie_verification'}
            )
            session.add(consent_log)
            await session.commit()
            
            if granted:
                await query.edit_message_text(
                    "✅ **Consent Granted**\n\n"
                    "Your face will be unblurred in this submission.\n\n"
                    "**Next Steps:**\n"
                    "1. Admin will review the submission\n"
                    "2. Final copy will be generated with your face unblurred\n"
                    "3. You'll be notified when published\n\n"
                    "To revoke consent: /forget",
                    parse_mode='Markdown'
                )
                
                # Trigger re-processing to regenerate final copy
                from tasks import enqueue_processing_job
                await enqueue_processing_job(submission_id, 'consent_update')
            else:
                await query.edit_message_text(
                    "✅ **Consent Denied**\n\n"
                    "Your face will remain blurred in this submission.\n"
                    "No further action is needed."
                )
    
    except Exception as e:
        logger.error(f"Consent handling failed: {e}")
        await query.edit_message_text("❌ Error processing consent. Please try again.")
    
    return ConversationHandler.END


async def request_third_party_consent(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                      face_id: str, submission_id: str, 
                                      target_telegram_username: str) -> bool:
    """
    Send consent request to third party (appears in uploader's photo but not the uploader).
    
    Args:
        face_id: Detected face UUID
        submission_id: Submission UUID
        target_telegram_username: Username of person in photo
    
    Returns:
        bool: True if consent request sent successfully
    """
    try:
        # Look up target's profile by username hash (in real app, you'd need a username index)
        async with async_session_factory() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(DetectedPerson).where(DetectedPerson.face_id == face_id)
            )
            detected_person = result.scalar_one_or_none()
            
            if not detected_person:
                return False
            
            # Update to requested status
            await session.execute(
                sa_update(DetectedPerson)
                .where(DetectedPerson.face_id == face_id)
                .values(
                    consent_status='requested',
                    consent_requested_at=datetime.utcnow(),
                    consent_telegram_id_hash=target_telegram_username  # In production, hash properly
                )
            )
            
            # Log request
            consent_log = ConsentLog(
                person_telegram_id_hash=target_telegram_username,
                submission_id=submission_id,
                face_id=face_id,
                action='requested',
                timestamp=datetime.utcnow(),
                consent_version=CURRENT_CONSENT_VERSION,
                details={'requested_by': context.user_data.get('profile_id')}
            )
            session.add(consent_log)
            await session.commit()
        
        # Send DM to target user (requires they've started the bot)
        try:
            await context.bot.send_message(
                chat_id=target_telegram_username,  # In production, use actual Telegram ID
                text=f"👤 **Consent Request**\n\n"
                     f"You appear in footage submitted by @{update.effective_user.username or 'anonymous'}.\n\n"
                     f"Would you like to approve unblurring of your face?\n\n"
                     f"[View Preview] [Approve] [Deny]",
                parse_mode='Markdown'
            )
            return True
        except Exception as e:
            logger.warning(f"Could not send DM to {target_telegram_username}: {e}")
            return False
    
    except Exception as e:
        logger.error(f"Third-party consent request failed: {e}")
        return False


async def handle_tagging(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Handle face tagging from uploader review.
    Called when uploader is reviewing blurred preview.
    """
    query = update.callback_query
    await query.answer()
    
    action = query.data
    submission_id = context.user_data.get('review_submission_id')
    
    if action == "tag_self":
        # Request selfie for verification
        face_id = context.user_data.get('review_face_id')
        await request_selfie_verification(update, context, face_id, submission_id)
        return 1
    
    elif action == "tag_official":
        # Mark as on-duty official (requires admin approval)
        await query.edit_message_text(
            "👮 **Official Designation**\n\n"
            "Please select the official category:\n",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚔 Police", callback_data="official_police")],
                [InlineKeyboardButton("🪖 Military", callback_data="official_military")],
                [InlineKeyboardButton("🚒 Fire/Rescue", callback_data="official_fire")],
                [InlineKeyboardButton("🏥 Medical", callback_data="official_medical")],
                [InlineKeyboardButton("Other", callback_data="official_other")],
            ])
        )
        return 3  # Official state
    
    elif action == "tag_other":
        # Tag as third party - request consent if Telegram ID known
        await query.edit_message_text(
            "👥 **Third-Party Consent**\n\n"
            "To request consent from this person:\n"
            "• They must have a Telegram account\n"
            "• They must have previously used this bot, or\n"
            "• You provide their Telegram username\n\n"
            "If their Telegram ID is unknown, the face will remain blurred by default.",
            parse_mode='Markdown'
        )
        return ConversationHandler.END
    
    return ConversationHandler.END


# Inline keyboard builders for admin review
def get_face_action_keyboard(face_id: str, is_minor: bool = False) -> InlineKeyboardMarkup:
    """Build keyboard for admin face review."""
    if is_minor:
        # Minor faces - no unblur allowed
        keyboard = [
            [InlineKeyboardButton("👁️ View Face", callback_data=f"view_{face_id}")],
            [InlineKeyboardButton("✅ Keep Blurred (Required)", callback_data=f"keep_blur_{face_id}")],
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("👁️ View Face", callback_data=f"view_{face_id}")],
            [InlineKeyboardButton("🔓 Unblur Face", callback_data=f"unblur_{face_id}")],
            [InlineKeyboardButton("🔒 Keep Blurred", callback_data=f"keep_blur_{face_id}")],
            [InlineKeyboardButton("❌ Mark Minor (Permanent Blur)", callback_data=f"mark_minor_{face_id}")],
        ]
    
    return InlineKeyboardMarkup(keyboard)


# Placeholder imports (should be in actual implementation)
import io
from sqlalchemy import update as sa_update
from telegram.ext import ConversationHandler


# Register handlers
def get_consent_handlers():
    """Return list of consent-related handlers."""
    return [
        CallbackQueryHandler(handle_tagging, pattern="^tag_(self|official|other)$"),
        CallbackQueryHandler(handle_self_consent, pattern="^consent_(grant|deny)_self$"),
    ]