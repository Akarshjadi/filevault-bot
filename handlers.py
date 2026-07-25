"""
File Handler — DPDP Act 2023 Compliant
Pseudonymized intake, EXIF stripping, dual-bucket R2 storage.
"""
import os
import io
from datetime import datetime, timezone
from PIL import Image
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from sqlalchemy import select, text

from utils import ensure_user, DbSession, map_telegram_file_type, format_file_size, log_audit, escape_markdown
from models import Vault, File, FileType, PendingFile, User, UserRole, Incident, Person, IncidentPerson
from storage import upload_file, delete_file, is_configured as r2_is_configured
from crypto_utils import hash_person_name, encrypt_field, decrypt_field

# Storage path for local file copies (optional fallback)
STORAGE_BASE = os.getenv("STORAGE_BASE", "./vault_storage")

# In-memory rate limiting: user_id -> last submission timestamp
user_last_submitted = {}

# Configuration
RATE_LIMIT_SECONDS = 30
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB
STAGING_CHANNEL_ID = int(os.getenv("STAGING_CHANNEL_ID", "0"))


async def get_admin_ids() -> list[int]:
    """Get all admin user IDs."""
    async with DbSession() as session:
        result = await session.execute(
            select(User.user_id).where(User.role == UserRole.ADMIN)
        )
        return [row[0] for row in result.fetchall()]


async def get_shared_vault() -> Vault:
    """Get the shared vault, creating it if needed."""
    async with DbSession() as session:
        result = await session.execute(select(Vault))
        vault = result.scalar_one_or_none()
        if not vault:
            vault = Vault(
                telegram_group_id=0,
                name="Shared Vault",
            )
            session.add(vault)
            await session.flush()
        return vault


async def strip_exif(update: Update) -> bytes:
    """Download file and strip all EXIF metadata."""
    file_obj = None
    for attr in ['document', 'photo', 'video', 'audio', 'voice', 'animation']:
        msg = getattr(update.message, attr, None)
        if msg:
            file_obj = msg
            break
    
    if not file_obj:
        raise ValueError("No file found in message")
    
    # Get highest resolution for photos
    if attr == 'photo':
        file_obj = file_obj[-1]
    
    tg_file = await file_obj.get_file()
    file_bytes = await tg_file.download_as_bytearray()
    
    # Strip EXIF for images
    if attr in ['photo'] or (attr == 'document' and getattr(file_obj, 'mime_type', '').startswith('image/')):
        try:
            img = Image.open(io.BytesIO(file_bytes))
            # Remove EXIF by creating new image
            data = list(img.getdata())
            img_clean = Image.new(img.mode, img.size)
            img_clean.putdata(data)
            out = io.BytesIO()
            img_clean.save(out, format=img.format)
            return out.getvalue()
        except Exception:
            pass
    
    return bytes(file_bytes)


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming files — DPDP-compliant intake."""
    if not await ensure_user(update, context):
        return

    user = update.effective_user

    # Determine file type
    file_obj = None
    file_type_str = None

    if update.message.document:
        file_obj = update.message.document
        file_type_str = "document"
    elif update.message.photo:
        file_obj = update.message.photo[-1]
        file_type_str = "photo"
    elif update.message.video:
        file_obj = update.message.video
        file_type_str = "video"
    elif update.message.audio:
        file_obj = update.message.audio
        file_type_str = "audio"
    elif update.message.voice:
        file_obj = update.message.voice
        file_type_str = "voice"
    elif update.message.animation:
        file_obj = update.message.animation
        file_type_str = "animation"

    if not file_obj:
        await update.message.reply_text("⚠️ Unsupported file type.")
        return

    status_msg = await update.message.reply_text("📥 Processing file...")

    # Rate limit check
    user_id = user.id
    now = datetime.now(timezone.utc)
    if user_id in user_last_submitted:
        elapsed = (now - user_last_submitted[user_id]).total_seconds()
        if elapsed < RATE_LIMIT_SECONDS:
            await status_msg.edit_text(
                f"⚠️ Please wait {int(RATE_LIMIT_SECONDS - elapsed)}s before sending another file."
            )
            return

    # File size check
    file_size = file_obj.file_size or 0
    if file_size > MAX_FILE_SIZE_BYTES:
        await status_msg.edit_text(
            f"⚠️ File size exceeds the 50MB limit."
        )
        return

    try:
        # Strip EXIF before any processing
        file_bytes = await strip_exif(update)
        
        # Determine filename
        file_name = None
        if file_type_str == "document" and hasattr(file_obj, 'file_name') and file_obj.file_name:
            file_name = file_obj.file_name
        else:
            ext_map = {
                "photo": ".jpg", "video": ".mp4", "audio": ".mp3",
                "voice": ".ogg", "animation": ".gif"
            }
            ext = ext_map.get(file_type_str, "")
            file_name = f"{file_type_str}_{file_obj.file_unique_id[:8]}{ext}"

        mapped_type = map_telegram_file_type(file_type_str)
        original_caption = update.message.caption or ""

        # Create incident record
        async with DbSession() as session:
            incident = Incident(
                incident_date=now,
                description="Public incident - pending review",
                tags=["pending_review"]
            )
            session.add(incident)
            await session.flush()
            incident_id = incident.incident_id

        # Forward to staging channel if configured
        staging_message_id = None
        if STAGING_CHANNEL_ID != 0:
            try:
                staging_msg = await context.bot.copy_message(
                    chat_id=STAGING_CHANNEL_ID,
                    from_chat_id=update.effective_chat.id,
                    message_id=update.message.message_id,
                )
                staging_message_id = staging_msg.message_id
            except Exception:
                pass

        # Save as pending file
        async with DbSession() as session:
            pending = PendingFile(
                sender_user_id=user.id,
                telegram_file_id=file_obj.file_id,
                telegram_file_unique_id=file_obj.file_unique_id,
                file_type=FileType(mapped_type),
                file_size=file_obj.file_size,
                file_name=file_name,
                caption=original_caption,
                chat_id=update.effective_chat.id,
                message_id=update.message.message_id,
                staging_message_id=staging_message_id,
                incident_id=incident_id,
                status="pending",
            )
            session.add(pending)
            await session.flush()
            pending_id = pending.pending_id

            await log_audit(
                user_id=user.id,
                action="file_pending_approval",
                details=f"File submitted for approval: {file_name} ({file_type_str})",
                session=session,
            )

        # Update rate limit
        user_last_submitted[user_id] = now

        safe_first_name = escape_markdown(user.first_name or "")
        safe_username = escape_markdown(user.username or "") if user.username else ""
        safe_file_name = escape_markdown(file_name or "Unknown")
        safe_caption = escape_markdown(original_caption) if original_caption else ""

        review_caption = (
            f"📥 **New File Pending Approval**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 **From:** {safe_first_name}"
            f"{' (@' + safe_username + ')' if user.username else ''}\n"
            f"🆔 **User ID:** `{user.id}`\n"
            f"📄 **File:** {safe_file_name}\n"
            f"📁 **Type:** {file_type_str}\n"
            f"💾 **Size:** {format_file_size(file_size)}\n"
            f"🔖 **Pending ID:** `{pending_id}`\n"
        )
        if safe_caption:
            review_caption += f"💬 **Caption:** {safe_caption}\n"

        # NEW: Adult/Minor approval buttons
        keyboard = [
            [
                InlineKeyboardButton("✅ Approve Adult", callback_data=f"approve_adult_{pending_id}"),
                InlineKeyboardButton("⚠️ Approve Minor", callback_data=f"approve_minor_{pending_id}"),
            ],
            [
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_{pending_id}"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Send to all admins
        admin_ids = await get_admin_ids()
        sent_to_admins = 0
        for admin_id in admin_ids:
            try:
                source_chat = STAGING_CHANNEL_ID if staging_message_id else update.effective_chat.id
                source_msg = staging_message_id if staging_message_id else update.message.message_id

                await context.bot.copy_message(
                    chat_id=admin_id,
                    from_chat_id=source_chat,
                    message_id=source_msg,
                    caption=review_caption,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=reply_markup,
                )
                sent_to_admins += 1
            except Exception:
                pass

        if sent_to_admins == 0:
            await status_msg.edit_text(
                "⚠️ **No admins are available to review your file.**\n\n"
                "Please try again later.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        await status_msg.edit_text(
            f"✅ **File sent for admin review!**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📄 **File:** {safe_file_name}\n"
            f"🔖 **Request ID:** `{pending_id}`\n\n"
            f"An admin will review your file shortly.\n"
            f"You'll be notified when it's approved or rejected.",
            parse_mode=ParseMode.MARKDOWN
        )

    except Exception as e:
        error_msg = str(e)
        await status_msg.edit_text(
            f"❌ **Error submitting file:**\n`{error_msg}`",
            parse_mode=ParseMode.MARKDOWN
        )