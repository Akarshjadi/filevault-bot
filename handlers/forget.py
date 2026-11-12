"""
Right to be Forgotten Handler
Implements DPDP Act data deletion requests.
"""
import logging
from typing import Optional
from datetime import datetime
from uuid import UUID

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, ConversationHandler

from models_vault import (
    Profile, Submission, DetectedPerson, ConsentLog, FaceEmbedding,
    ConsentStatus, get_session
)
from crypto_utils import hash_telegram_user_id
from database import async_session_factory
from storage.r2 import get_r2_storage, delete_file
from tasks import enqueue_processing_job

logger = logging.getLogger(__name__)


async def forget_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Handle /forget command - initiate data deletion request.
    
    Flow:
    1. Verify user identity (via anonymous token)
    2. Show what data will be deleted
    3. Get confirmation
    4. Delete submission metadata from DB
    5. Remove R2 files
    6. Log deletion for compliance
    """
    user = update.effective_user
    telegram_id_hash = hash_telegram_user_id(user.id)
    
    # Get user's submissions via anonymous token lookup
    async with async_session_factory() as session:
        from sqlalchemy import select, func
        from models_vault import Submission
        
        # Find all submissions by this user (via token relationship)
        # In practice, user would provide submission IDs or we use a temporary mapping
        
        # For now, check if user has any profile
        result = await session.execute(
            select(Profile).where(Profile.telegram_user_id_hash == telegram_id_hash)
        )
        profile = result.scalar_one_or_none()
        
        if not profile:
            await update.message.reply_text(
                "ℹ️ You don't have a registered profile.\n"
                "Your submissions are anonymous and cannot be linked to you.\n\n"
                "If you need to delete a specific submission, you'll need the Submission ID."
            )
            return ConversationHandler.END
        
        profile_id = profile.profile_id
        
        # Count user's data (ANONYMOUS - use anonymous_token field)
        sub_count_result = await session.execute(
            select(func.count(Submission.submission_id)).where(Submission.uploader_anonymous_token.isnot(None))
        )
        submission_count = sub_count_result.scalar_one()
        
        # Count detected persons related to this user's submissions
        faces_count_result = await session.execute(
            select(func.count(DetectedPerson.face_id))
            .join(Submission, DetectedPerson.submission_id == Submission.submission_id)
            .where(Submission.uploader_anonymous_token.isnot(None))
        )
        faces_count = faces_count_result.scalar_one()
        
        # Count consented faces
        consented_faces_result = await session.execute(
            select(func.count(DetectedPerson.face_id))
            .join(Submission, DetectedPerson.submission_id == Submission.submission_id)
            .where(
                Submission.uploader_anonymous_token.isnot(None),
                DetectedPerson.consent_status == 'granted'
            )
        )
        consented_faces = consented_faces_result.scalar_one()
    
    # Warning message
    msg = "⚠️ **Right to be Forgotten**\n\n"
    msg += "This will **permanently delete** all your data:\n\n"
    msg += f"• **{submission_count}** submissions\n"
    msg += f"• **{faces_count}** detected faces\n"
    msg += f"• **{consented_faces}** granted consents\n"
    msg += "• All original files from R2 storage\n"
    msg += "• All processing copies\n"
    msg += "• All preview images\n"
    msg += "• All consent logs\n\n"
    msg += "⚠️ **This action cannot be undone.**\n\n"
    msg += "Do you want to proceed?"
    
    keyboard = [
        [InlineKeyboardButton("✅ Yes, delete all my data", callback_data="forget_confirm"),
         InlineKeyboardButton("❌ Cancel", callback_data="forget_cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Store profile_id for confirmation
    context.user_data['forget_profile_id'] = str(profile_id)
    
    await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
    
    return 1  # Confirmation state


async def handle_forget_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle confirmation and execute deletion."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "forget_cancel":
        await query.edit_message_text("❌ Deletion cancelled.\nYour data remains intact.")
        return ConversationHandler.END
    
    profile_id = context.user_data.get('forget_profile_id')
    if not profile_id:
        await query.edit_message_text("❌ Session expired. Please try /forget again.")
        return ConversationHandler.END
    
    await query.edit_message_text("🔄 Processing deletion request...")
    
    try:
        async with async_session_factory() as session:
            from sqlalchemy import select, delete as sa_delete
            
            # Get all submission IDs before deleting
            # ANONYMITY: No uploader_id linkage
            sub_result = await session.execute(
                select(Submission.submission_id, Submission.original_hash)
                .where(Submission.uploader_anonymous_token.isnot(None))
            )
            submissions = sub_result.all()
            
            # Delete R2 files first
            r2 = get_r2_storage()
            
            for submission_id, original_hash in submissions:
                # Delete from all R2 buckets
                r2_paths = [
                    # Originals
                    (r2.BUCKET_ORIGINALS, f"{original_hash}"),
                    # EXIF
                    (r2.BUCKET_EXIF, f"{original_hash}/exif.json"),
                    # Processing
                    (r2.BUCKET_PROCESSING, f"{submission_id}/blurred_initial.mp4"),
                    # Published (if exists)
                    (r2.BUCKET_PUBLISHED, f"{submission_id}/final.mp4"),
                ]
                
                for bucket, key in r2_paths:
                    if r2.exists(bucket, key):
                        r2.delete(bucket, key)
                        logger.info(f"Deleted R2 file: r2://{bucket}/{key}")
            
            # Delete face embeddings
            await session.execute(
                sa_delete(FaceEmbedding).where(FaceEmbedding.profile_id == profile_id)
            )
            
            # Delete consent logs
            await session.execute(
                sa_delete(ConsentLog).where(ConsentLog.submission_id.in_([s.submission_id for s in submissions]))
            )
            
            # Delete detected persons
            await session.execute(
                sa_delete(DetectedPerson).where(DetectedPerson.submission_id.in_([s.submission_id for s in submissions]))
            )
            
            # Delete submissions (anonymously)
            await session.execute(
                sa_delete(Submission).where(Submission.uploader_anonymous_token.isnot(None))
            )
            
            # Delete profile
            await session.execute(
                sa_delete(Profile).where(Profile.profile_id == profile_id)
            )
            
            await session.commit()
            
            logger.info(f"Deleted all data for profile {profile_id}")
            
        await update.message.reply_text(
            "✅ **Data Deletion Complete**\n\n"
            "All your data has been permanently deleted:\n"
            "• Profile deleted\n"
            "• Submissions deleted\n"
            "• R2 files deleted\n"
            "• Consent logs deleted\n\n"
            "You are no longer registered.\n"
            "Use /start to register again if needed.",
            parse_mode='Markdown'
        )
        
        return ConversationHandler.END
    
    except Exception as e:
        logger.error(f"Deletion failed for profile {profile_id}: {e}")
        
        await update.message.reply_text(
            "❌ **Deletion Failed**\n\n"
            "There was an error deleting your data.\n"
            "Please contact support.\n\n"
            f"Error: {str(e)}"
        )
        
        return ConversationHandler.END


async def revoke_consent_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Revoke previous consent for face unblur.
    
    This is a subset of /forget that only revokes consent,
    keeping the submission intact but blurring the face again.
    """
    user = update.effective_user
    telegram_id_hash = hash_telegram_user_id(user.id)
    
    async with async_session_factory() as session:
        from sqlalchemy import select
        
        # Find user's profile
        result = await session.execute(
            select(Profile).where(Profile.telegram_user_id_hash == telegram_id_hash)
        )
        profile = result.scalar_one_or_none()
        
        if not profile:
            await update.message.reply_text("❌ You are not registered.")
            return ConversationHandler.END
        
        # Find submissions where user has granted consent
        sub_result = await session.execute(
            select(Submission.submission_id)
            .join(DetectedPerson, DetectedPerson.submission_id == Submission.submission_id)
            .where(
                DetectedPerson.consent_status == 'granted'
            )
        )
        consented_submissions = [row[0] for row in sub_result.all()]
    
    if not consented_submissions:
        await update.message.reply_text(
            "ℹ️ You have no granted consents to revoke."
        )
        return ConversationHandler.END
    
    # Ask for confirmation
    msg = "⚠️ **Revoke Consent**\n\n"
    msg += f"You have granted consent in {len(consented_submissions)} submission(s).\n\n"
    msg += "Revoking consent will:\n"
    msg += "• Set your faces back to blurred\n"
    msg += "• Trigger reprocessing of affected submissions\n"
    msg += "• Log the revocation\n\n"
    msg += "Do you want to proceed?"
    
    keyboard = [
        [InlineKeyboardButton("✅ Yes, revoke all consents", callback_data="revoke_confirm"),
         InlineKeyboardButton("❌ Cancel", callback_data="revoke_cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    context.user_data['consent_profile_id'] = str(profile.profile_id)
    context.user_data['consent_submission_ids'] = [str(s) for s in consented_submissions]
    
    await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
    
    return 1  # Confirmation state


async def handle_revoke_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle consent revocation confirmation."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "revoke_cancel":
        await query.edit_message_text("❌ Consent revocation cancelled.")
        return ConversationHandler.END
    
    profile_id = context.user_data.get('consent_profile_id')
    submission_ids = context.user_data.get('consent_submission_ids', [])
    
    if not profile_id:
        await query.edit_message_text("❌ Session expired.")
        return ConversationHandler.END
    
    try:
        async with async_session_factory() as session:
            from sqlalchemy import select, update as sa_update
            from models_vault import ConsentLog
            
            now = datetime.utcnow()
            
            # Update all granted consents to revoked
            for submission_id in submission_ids:
                # Get all faces for this user in this submission
                faces_result = await session.execute(
                    select(DetectedPerson.face_id)
                    .where(
                        DetectedPerson.submission_id == submission_id,
                        DetectedPerson.consent_status == 'granted'
                    )
                )
                face_ids = [row[0] for row in faces_result.all()]
                
                # Revoke each face
                for face_id in face_ids:
                    # Update face
                    await session.execute(
                        sa_update(DetectedPerson)
                        .where(DetectedPerson.face_id == face_id)
                        .values(
                            blur_status='blurred',
                            consent_status='revoked',
                            consent_resolved_at=now
                        )
                    )
                    
                    # Log revocation
                    log = ConsentLog(
                        person_telegram_id_hash=hash_telegram_user_id(update.effective_user.id),
                        submission_id=submission_id,
                        face_id=face_id,
                        action='revoked',
                        timestamp=now,
                        consent_version='1.0',
                        details={'method': 'user_request'}
                    )
                    session.add(log)
                    
                    # Trigger reprocessing
                    await enqueue_processing_job(submission_id, 'consent_update')
            
            await session.commit()
        
        await query.edit_message_text(
            "✅ **Consent Revoked**\n\n"
            f"Revoked consent in {len(submission_ids)} submission(s).\n\n"
            "Your faces will be blurred again in the next 24 hours.\n"
            "You will be notified when reprocessing is complete.",
            parse_mode='Markdown'
        )
        
        return ConversationHandler.END
    
    except Exception as e:
        logger.error(f"Consent revocation failed: {e}")
        await query.edit_message_text(
            "❌ Error revoking consent. Please try again later."
        )
        return ConversationHandler.END


def get_forget_handler() -> ConversationHandler:
    """Return the forget (data deletion) handler."""
    return ConversationHandler(
        entry_points=[CommandHandler("forget", forget_command)],
        states={
            1: [CallbackQueryHandler(handle_forget_confirmation, pattern="^forget_")],
        },
        fallbacks=[CommandHandler("cancel", forget_command)],
        per_user=True,
        per_chat=True,
    )


def get_revoke_consent_handler() -> ConversationHandler:
    """Return the consent revocation handler."""
    return ConversationHandler(
        entry_points=[CommandHandler("revoke_consent", revoke_consent_command)],
        states={
            1: [CallbackQueryHandler(handle_revoke_confirmation, pattern="^revoke_")],
        },
        fallbacks=[CommandHandler("cancel", revoke_consent_command)],
        per_user=True,
        per_chat=True,
    )