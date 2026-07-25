"""
Admin Handler
Manages admin review of submissions, face approval, and system moderation.
"""
import logging
from typing import Optional, Dict, List
from datetime import datetime

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler

from models_vault import (
    Profile, Submission, DetectedPerson, AdminAuditLog, 
    BlurStatus, SubjectType, ConsentStatus, get_session
)
from crypto_utils import hash_telegram_user_id
from database import async_session_factory
from storage.r2 import get_r2_storage, get_presigned_url
from processing.generate_preview import PreviewGenerator

logger = logging.getLogger(__name__)

# Admin Telegram IDs (set from environment or config)
ADMIN_IDS = set()


def set_admin_ids(admin_ids: List[int]):
    """Set list of admin Telegram IDs."""
    global ADMIN_IDS
    ADMIN_IDS = set(admin_ids)


def is_admin(user_id: int) -> bool:
    """Check if user is admin."""
    return user_id in ADMIN_IDS


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Admin dashboard - /admin"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ Unauthorized. Admin access required.")
        return ConversationHandler.END
    
    keyboard = [
        [InlineKeyboardButton("📋 Pending Reviews", callback_data="admin_pending")],
        [InlineKeyboardButton("📊 Statistics", callback_data="admin_stats")],
        [InlineKeyboardButton("🔍 Search Submission", callback_data="admin_search")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="admin_settings")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👮 **Admin Dashboard**\n\n"
        "Select an action:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    
    return 1  # Admin menu state


async def admin_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle admin menu selections."""
    query = update.callback_query
    await query.answer()
    
    action = query.data
    
    if action == "admin_pending":
        await show_pending_reviews(update, context)
        return 2  # Review state
    
    elif action == "admin_stats":
        await show_statistics(update, context)
        return 1  # Back to menu
    
    elif action == "admin_search":
        await query.edit_message_text(
            "🔍 **Search Submission**\n\n"
            "Send the submission ID to search.\n"
            "Format: UUID or partial ID"
        )
        return 3  # Search state
    
    elif action == "admin_settings":
        await show_admin_settings(update, context)
        return 4  # Settings state
    
    return 1


async def show_pending_reviews(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                               page: int = 1, per_page: int = 5):
    """Show list of submissions pending review."""
    query = update.callback_query
    
    async with async_session_factory() as session:
        from sqlalchemy import select, func
        
        # Get total count
        count_result = await session.execute(
            select(func.count(Submission.submission_id))
            .where(Submission.status == 'pending_review')
        )
        total = count_result.scalar_one()
        
        # Get paginated results
        offset = (page - 1) * per_page
        result = await session.execute(
            select(Submission, Profile)
            .join(Profile, Submission.uploader_id == Profile.profile_id)
            .where(Submission.status == 'pending_review')
            .order_by(Submission.created_at.desc())
            .limit(per_page)
            .offset(offset)
        )
        submissions = result.all()
        
        if not submissions:
            await query.edit_message_text(
                "✅ No pending reviews.\n\nAll submissions have been reviewed."
            )
            return 1
        
        # Build message
        msg = f"📋 **Pending Reviews** (Page {page}/{max(1, (total + per_page - 1) // per_page)})\n\n"
        
        for submission, profile in submissions:
            msg += f"**Submission:** `{submission.submission_id[:8]}...`\n"
            msg += f"**Type:** {submission.file_type}\n"
            msg += f"**Status:** {submission.status}\n"
            msg += f"**Submitted:** {submission.created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
        
        # Navigation
        keyboard = []
        for submission, profile in submissions:
            keyboard.append([
                InlineKeyboardButton(
                    f"Review {submission.submission_id[:8]}...",
                    callback_data=f"review_{submission.submission_id}"
                )
            ])
        
        if page > 1:
            keyboard.append([InlineKeyboardButton("◀️ Previous", callback_data=f"page_{page-1}")])
        if offset + per_page < total:
            keyboard.append([InlineKeyboardButton("Next ▶️", callback_data=f"page_{page+1}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="admin_menu")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if query:
            await query.edit_message_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode='Markdown')


async def review_submission(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                           submission_id: str) -> int:
    """Review a specific submission."""
    query = update.callback_query
    await query.answer()
    
    admin_id = update.effective_user.id
    
    async with async_session_factory() as session:
        from sqlalchemy import select
        
        # Get submission with detected persons
        result = await session.execute(
            select(Submission, Profile)
            .join(Profile, Submission.uploader_id == Profile.profile_id)
            .where(Submission.submission_id == submission_id)
        )
        submission, uploader = result.first()
        
        if not submission:
            await query.edit_message_text("❌ Submission not found.")
            return await show_pending_reviews(update, context)
        
        # Get detected persons
        persons_result = await session.execute(
            select(DetectedPerson).where(DetectedPerson.submission_id == submission_id)
        )
        detected_persons = persons_result.scalars().all()
        
        # Log admin view
        audit_log = AdminAuditLog(
            admin_id=hash_telegram_user_id(admin_id),
            submission_id=submission_id,
            action='view',
            timestamp=datetime.utcnow(),
            details={'admin_user_id': admin_id}
        )
        session.add(audit_log)
        await session.commit()
    
    # Build review message
    msg = f"🔍 **Submission Review**\n\n"
    msg += f"**ID:** `{submission_id[:16]}...`\n"
    msg += f"**Type:** {submission.file_type}\n"
    msg += f"**Status:** {submission.status}\n"
    msg += f"**Uploaded by:** {uploader.first_name or 'Unknown'}\n"
    msg += f"**Date:** {submission.created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
    
    msg += f"**Detected Faces:** {len(detected_persons)}\n"
    
    # Face summary
    faces_blurred = sum(1 for p in detected_persons if p.blur_status == 'blurred')
    faces_unblurred = sum(1 for p in detected_persons if p.blur_status == 'unblurred')
    faces_minor = sum(1 for p in detected_persons if p.is_minor)
    faces_official = sum(1 for p in detected_persons if p.subject_type == 'official')
    
    msg += f"• Blurred: {faces_blurred}\n"
    msg += f"• Unblurred: {faces_unblurred}\n"
    msg += f"• Official (pending): {faces_official}\n"
    msg += f"• Minors: {faces_minor}\n\n"
    
    msg += "**File Links:**\n"
    msg += f"• Original: [Private]\n"
    msg += f"• Preview: [Preview]\n"
    
    # Action buttons
    keyboard = [
        [InlineKeyboardButton("✅ Approve & Publish", callback_data=f"approve_{submission_id}"),
         InlineKeyboardButton("❌ Reject", callback_data=f"reject_{submission_id}")],
        [InlineKeyboardButton("👁️ Review Faces", callback_data=f"faces_{submission_id}")],
        [InlineKeyboardButton("📝 Edit Metadata", callback_data=f"edit_{submission_id}")],
        [InlineKeyboardButton("🔙 Back to List", callback_data="admin_pending")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Store submission_id for later
    context.user_data['review_submission_id'] = submission_id
    
    await query.edit_message_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
    
    return 2  # Review state


async def review_faces(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show detected faces for review."""
    query = update.callback_query
    await query.answer()
    
    submission_id = context.user_data.get('review_submission_id')
    if not submission_id:
        await query.edit_message_text("❌ No submission selected.")
        return await show_pending_reviews(update, context)
    
    async with async_session_factory() as session:
        from sqlalchemy import select
        
        result = await session.execute(
            select(DetectedPerson).where(DetectedPerson.submission_id == submission_id)
        )
        faces = result.scalars().all()
        
        if not faces:
            await query.edit_message_text("No faces detected.")
            return await review_submission(update, context, submission_id)
        
        # Show first face
        face = faces[0]
        context.user_data['review_face_index'] = 0
        context.user_data['review_faces'] = [str(f.face_id) for f in faces]
        
        await show_face_detail(update, context, face, 0, len(faces))


async def show_face_detail(update: Update, context: ContextTypes.DEFAULT_TYPE,
                           face: DetectedPerson, index: int, total: int):
    """Show detail for a single face."""
    query = update.callback_query
    
    # Get presigned preview URL
    r2 = get_r2_storage()
    preview_url = r2.get_signed_url(
        r2.BUCKET_PROCESSING,
        f"{face.submission_id}/blurred_initial.mp4",
        expires_in=3600
    )
    
    msg = f"👤 **Face {index + 1} of {total}**\n\n"
    msg += f"**Face ID:** `{str(face.face_id)[:16]}...`\n"
    msg += f"**Type:** {face.subject_type}\n"
    msg += f"**Blur Status:** {face.blur_status}\n"
    msg += f"**Consent Status:** {face.consent_status}\n"
    msg += f"**Is Minor:** {'⚠️ Yes' if face.is_minor else 'No'}\n"
    msg += f"**Admin Approved:** {'Yes' if face.admin_approved else 'No'}\n\n"
    
    if face.subject_type == 'official' and not face.admin_approved:
        msg += "⚠️ **Awaiting admin approval for official tag**\n"
    
    msg += f"[View Preview]({preview_url})"
    
    # Navigation buttons
    keyboard = []
    
    # Face actions
    if not face.is_minor:
        if face.blur_status == 'blurred' and face.consent_status != 'granted':
            keyboard.append([
                InlineKeyboardButton("🔓 Unblur", callback_data=f"unblur_{face.face_id}"),
                InlineKeyboardButton("❌ Mark Minor", callback_data=f"mark_minor_{face.face_id}")
            ])
        
        if face.subject_type != 'official':
            keyboard.append([
                InlineKeyboardButton("👮 Mark Official", callback_data=f"official_{face.face_id}")
            ])
    
    # Navigation
    nav_buttons = []
    if index > 0:
        nav_buttons.append(InlineKeyboardButton("◀️ Previous", callback_data=f"prev_face"))
    if index < total - 1:
        nav_buttons.append(InlineKeyboardButton("Next ▶️", callback_data=f"next_face"))
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    keyboard.append([InlineKeyboardButton("🔙 Back to Submission", callback_data=f"review_{face.submission_id}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(msg, reply_markup=reply_markup, parse_mode='Markdown')


async def navigate_faces(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Navigate between faces."""
    query = update.callback_query
    await query.answer()
    
    direction = query.data
    current_index = context.user_data.get('review_face_index', 0)
    face_ids = context.user_data.get('review_faces', [])
    
    if not face_ids:
        await query.edit_message_text("❌ No faces to review.")
        return 2
    
    if direction == "prev_face" and current_index > 0:
        current_index -= 1
    elif direction == "next_face" and current_index < len(face_ids) - 1:
        current_index += 1
    
    context.user_data['review_face_index'] = current_index
    
    # Get face from DB
    async with async_session_factory() as session:
        result = await session.execute(
            select(DetectedPerson).where(DetectedPerson.face_id == face_ids[current_index])
        )
        face = result.scalar_one_or_none()
    
    if face:
        await show_face_detail(update, context, face, current_index, len(face_ids))
    
    return 2


async def approve_submission(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                            submission_id: str) -> int:
    """Approve submission for publication."""
    query = update.callback_query
    await query.answer()
    
    admin_id = update.effective_user.id
    
    try:
        async with async_session_factory() as session:
            from sqlalchemy import select, update as sa_update
            
            # Update submission status
            await session.execute(
                sa_update(Submission)
                .where(Submission.submission_id == submission_id)
                .values(status='published', published_at=datetime.utcnow())
            )
            
            # Log admin action
            audit = AdminAuditLog(
                admin_id=hash_telegram_user_id(admin_id),
                submission_id=submission_id,
                action='approve',
                timestamp=datetime.utcnow(),
                details={'admin_user_id': admin_id, 'reason': 'Approved for publication'}
            )
            session.add(audit)
            await session.commit()
        
        # Trigger final processing (generate published copy)
        from tasks import enqueue_processing_job
        await enqueue_processing_job(submission_id, 'finalize_publish')
        
        await query.edit_message_text(
            "✅ **Submission Approved**\n\n"
            "The submission has been approved for publication.\n"
            "Final copy will be generated shortly.",
            parse_mode='Markdown'
        )
        
        return await show_pending_reviews(update, context)
    
    except Exception as e:
        logger.error(f"Approval failed: {e}")
        await query.edit_message_text("❌ Error approving submission.")
        return 1


async def reject_submission(update: Update, context: ContextTypes.DEFAULT_TYPE,
                           submission_id: str) -> int:
    """Reject submission and delete all files."""
    query = update.callback_query
    await query.answer()
    
    admin_id = update.effective_user.id
    
    try:
        async with async_session_factory() as session:
            from sqlalchemy import select, update as sa_update
            
            # Update status
            await session.execute(
                sa_update(Submission)
                .where(Submission.submission_id == submission_id)
                .values(status='rejected')
            )
            
            # Log admin action
            audit = AdminAuditLog(
                admin_id=hash_telegram_user_id(admin_id),
                submission_id=submission_id,
                action='reject',
                timestamp=datetime.utcnow(),
                details={'admin_user_id': admin_id}
            )
            session.add(audit)
            await session.commit()
        
        # TODO: Delete R2 files (originals, processing, previews)
        
        await query.edit_message_text(
            "❌ **Submission Rejected**\n\n"
            "The submission has been rejected and will be deleted.",
            parse_mode='Markdown'
        )
        
        return await show_pending_reviews(update, context)
    
    except Exception as e:
        logger.error(f"Rejection failed: {e}")
        await query.edit_message_text("❌ Error rejecting submission.")
        return 1


async def admin_face_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle admin actions on faces."""
    query = update.callback_query
    await query.answer()
    
    action_parts = query.data.split('_', 1)
    action = action_parts[0]
    face_id = action_parts[1] if len(action_parts) > 1 else None
    
    if not face_id:
        await query.edit_message_text("❌ Invalid face ID.")
        return 2
    
    admin_id = update.effective_user.id
    
    async with async_session_factory() as session:
        from sqlalchemy import select, update as sa_update
        
        result = await session.execute(
            select(DetectedPerson).where(DetectedPerson.face_id == face_id)
        )
        face = result.scalar_one_or_none()
        
        if not face:
            await query.edit_message_text("❌ Face not found.")
            return 2
        
        now = datetime.utcnow()
        
        if action == "unblur":
            # Admin override - unblur face
            await session.execute(
                sa_update(DetectedPerson)
                .where(DetectedPerson.face_id == face_id)
                .values(
                    blur_status='unblurred',
                    admin_approved=True,
                    admin_approved_at=now
                )
            )
            
            # Log action
            audit = AdminAuditLog(
                admin_id=hash_telegram_user_id(admin_id),
                submission_id=face.submission_id,
                action='unblur_face',
                timestamp=now,
                details={'face_id': face_id, 'reason': 'Admin approved'}
            )
            session.add(audit)
            await session.commit()
            
            # Trigger re-processing
            from tasks import enqueue_processing_job
            await enqueue_processing_job(str(face.submission_id), 'consent_update')
            
            await query.edit_message_text(
                "✅ **Face Unblurred**\n\n"
                "The face has been unblurred by admin.\n"
                "Final copy will be regenerated.",
                parse_mode='Markdown'
            )
        
        elif action == "keep_blur":
            await query.edit_message_text("✅ Face will remain blurred.")
        
        elif action == "mark_minor":
            # Mark as minor - permanently blurred
            await session.execute(
                sa_update(DetectedPerson)
                .where(DetectedPerson.face_id == face_id)
                .values(
                    is_minor=True,
                    blur_status='blurred',
                    consent_status='denied'  # Override any consent
                )
            )
            
            # Log action
            audit = AdminAuditLog(
                admin_id=hash_telegram_user_id(admin_id),
                submission_id=face.submission_id,
                action='mark_minor',
                timestamp=now,
                details={'face_id': face_id}
            )
            session.add(audit)
            await session.commit()
            
            # Trigger re-processing
            from tasks import enqueue_processing_job
            await enqueue_processing_job(str(face.submission_id), 'consent_update')
            
            await query.edit_message_text(
                "⚠️ **Face Marked as Minor**\n\n"
                "This face will remain permanently blurred.\n"
                "All consent overrides have been revoked.",
                parse_mode='Markdown'
            )
    
    return 2


async def show_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show admin statistics."""
    query = update.callback_query
    
    async with async_session_factory() as session:
        from sqlalchemy import select, func
        
        # Count submissions by status
        result = await session.execute(
            select(Submission.status, func.count(Submission.submission_id))
            .group_by(Submission.status)
        )
        status_counts = {status: count for status, count in result.all()}
        
        # Count total users
        users_result = await session.execute(
            select(func.count(Profile.profile_id))
        )
        total_users = users_result.scalar_one()
        
        # Count pending reviews
        pending_result = await session.execute(
            select(func.count(Submission.submission_id))
            .where(Submission.status == 'pending_review')
        )
        pending_reviews = pending_result.scalar_one()
        
        # Count faces
        faces_result = await session.execute(
            select(func.count(DetectedPerson.face_id))
        )
        total_faces = faces_result.scalar_one()
    
    msg = "📊 **System Statistics**\n\n"
    msg += f"**Users:** {total_users}\n"
    msg += f"**Pending Reviews:** {pending_reviews}\n"
    msg += f"**Total Faces Detected:** {total_faces}\n\n"
    
    msg += "**Submission Status:**\n"
    for status, count in status_counts.items():
        msg += f"• {status}: {count}\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="admin_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(msg, reply_markup=reply_markup, parse_mode='Markdown')


async def show_admin_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show admin settings."""
    query = update.callback_query
    
    keyboard = [
        [InlineKeyboardButton("👮 Manage Admins", callback_data="manage_admins")],
        [InlineKeyboardButton("📢 Broadcast Message", callback_data="broadcast")],
        [InlineKeyboardButton("🔄 Retry Failed Jobs", callback_data="retry_jobs")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="admin_menu")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "⚙️ **Admin Settings**\n\nSelect an option:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def search_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Search for submission by ID."""
    query = update.message.text.strip()
    
    async with async_session_factory() as session:
        from sqlalchemy import select
        
        # Try exact match first, then partial
        result = await session.execute(
            select(Submission).where(Submission.submission_id.ilike(f"%{query}%"))
        )
        submissions = result.scalars().all()
        
        if not submissions:
            await update.message.reply_text("❌ No submissions found.")
            return 1
        
        if len(submissions) == 1:
            await review_submission(update, context, str(submissions[0].submission_id))
            return 2
    
    # Multiple results
    keyboard = []
    for sub in submissions:
        keyboard.append([
            InlineKeyboardButton(
                f"{sub.submission_id[:8]}... ({sub.status})",
                callback_data=f"review_{sub.submission_id}"
            )
        ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"Found {len(submissions)} submissions:",
        reply_markup=reply_markup
    )
    
    return 2


def get_admin_handler() -> ConversationHandler:
    """Return admin conversation handler."""
    return ConversationHandler(
        entry_points=[CommandHandler("admin", admin_command)],
        states={
            1: [CallbackQueryHandler(admin_menu_callback, pattern="^admin_")],
            2: [CallbackQueryHandler(review_submission, pattern="^review_"),
                CallbackQueryHandler(review_faces, pattern="^faces_"),
                CallbackQueryHandler(navigate_faces, pattern="^(prev_face|next_face)$"),
                CallbackQueryHandler(admin_face_action, pattern="^(unblur|keep_blur|mark_minor|official)_"),
                CallbackQueryHandler(approve_submission, pattern="^approve_"),
                CallbackQueryHandler(reject_submission, pattern="^reject_")],
            3: [MessageHandler(filters.TEXT & ~filters.COMMAND, search_submission)],
        },
        fallbacks=[CommandHandler("cancel", admin_command)],
        per_user=True,
        per_chat=True,
    )


# Admin audit logging decorator
def log_admin_action(action: str):
    """Decorator to log admin actions."""
    def decorator(func):
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            admin_id = update.effective_user.id
            
            try:
                result = await func(update, context, *args, **kwargs)
                
                # Log successful action
                async with async_session_factory() as session:
                    audit = AdminAuditLog(
                        admin_id=hash_telegram_user_id(admin_id),
                        action=action,
                        timestamp=datetime.utcnow(),
                        details={'success': True, 'user_id': admin_id}
                    )
                    session.add(audit)
                    await session.commit()
                
                return result
            
            except Exception as e:
                logger.error(f"Admin action {action} failed: {e}")
                raise
        
        return wrapper
    return decorator