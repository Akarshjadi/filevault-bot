"""
File Handler — Core save flow using copyMessage to vault supergroup.
Logs metadata to PostgreSQL and provides inline action buttons.
Includes approval gate and audit logging.
"""
import os
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from sqlalchemy import select, text

from utils import ensure_user, DbSession, map_telegram_file_type, format_file_size, log_audit
from models import Vault, File, FileType

# Storage path for local file copies (optional fallback)
STORAGE_BASE = os.getenv("STORAGE_BASE", "./vault_storage")


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming files — save to vault supergroup via copyMessage, log metadata."""
    if not await ensure_user(update, context):
        return

    user = update.effective_user

    # Find user's vault
    async with DbSession() as session:
        result = await session.execute(
            select(Vault).where(Vault.owner_user_id == user.id)
        )
        vault = result.scalar_one_or_none()

    if not vault:
        await update.message.reply_text(
            "⚠️ **Vault not set up yet!**\n\n"
            "An admin needs to:\n"
            "1. Create a supergroup\n"
            "2. Add me as admin to that group\n"
            "3. Run `/setvault` inside that group\n\n"
            "Then I can start saving files!",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Determine file type and get file object
    file_obj = None
    file_type_str = None

    if update.message.document:
        file_obj = update.message.document
        file_type_str = "document"
    elif update.message.photo:
        file_obj = update.message.photo[-1]  # Highest resolution
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

    # Send "processing" message
    status_msg = await update.message.reply_text("📥 Processing...")

    try:
        # Build caption with metadata
        original_caption = update.message.caption or ""
        meta_caption = (
            f"📤 **{user.first_name}**"
            f"{' (@' + user.username + ')' if user.username else ''}\n"
            f"📁 `{file_type_str}` | 💾 {format_file_size(file_obj.file_size or 0)}"
        )
        if original_caption:
            meta_caption += f"\n💬 {original_caption}"
        if file_type_str == "document" and hasattr(file_obj, 'file_name') and file_obj.file_name:
            meta_caption = f"📄 `{file_obj.file_name}`\n" + meta_caption

        # Use copyMessage to push to vault group (avoids "Forwarded from" tag)
        copied_msg = await context.bot.copy_message(
            chat_id=vault.telegram_group_id,
            from_chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
            caption=meta_caption,
            parse_mode=ParseMode.MARKDOWN,
        )

        vault_message_id = copied_msg.message_id

        # Determine filename
        file_name = None
        if file_type_str == "document" and hasattr(file_obj, 'file_name') and file_obj.file_name:
            file_name = file_obj.file_name
        else:
            # Generate a descriptive name
            ext_map = {
                "photo": ".jpg", "video": ".mp4", "audio": ".mp3",
                "voice": ".ogg", "animation": ".gif"
            }
            ext = ext_map.get(file_type_str, "")
            file_name = f"{file_type_str}_{file_obj.file_unique_id[:8]}{ext}"

        # Map to our FileType enum
        mapped_type = map_telegram_file_type(file_type_str)

        # Log to database
        async with DbSession() as session:
            file_record = File(
                telegram_file_id=file_obj.file_id,
                telegram_file_unique_id=file_obj.file_unique_id,
                vault_message_id=vault_message_id,
                vault_id=vault.vault_id,
                sender_user_id=user.id,
                file_type=FileType(mapped_type),
                file_size=file_obj.file_size,
                file_name=file_name,
                caption=original_caption,
                tags=[],
                topic_id=None,
            )
            session.add(file_record)
            await session.flush()
            file_record_id = file_record.file_id_pk

            # Log audit
            await log_audit(
                user_id=user.id,
                action="upload",
                file_id=file_record_id,
                details=f"Uploaded {file_type_str}: {file_name}",
                session=session,
            )

        # Also download local copy for backup (if storage base is set)
        if STORAGE_BASE:
            try:
                file_data = await file_obj.get_file()
                local_path = os.path.join(STORAGE_BASE, str(file_record_id))
                await file_data.download_to_drive(local_path)
            except Exception:
                pass  # Non-critical — the file is in the vault group

        # Show inline action buttons
        keyboard = [
            [
                InlineKeyboardButton("🏷️ Tag", callback_data=f"tag_{file_record_id}"),
                InlineKeyboardButton("🗑️ Delete", callback_data=f"delete_{file_record_id}"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await status_msg.edit_text(
            f"✅ **File Saved!**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📄 **File:** {file_name}\n"
            f"🔗 **ID:** `{file_record_id}`\n"
            f"💾 **Size:** {format_file_size(file_obj.file_size or 0)}\n"
            f"📁 **Type:** {file_type_str}\n"
            f"🏛️ **Vault:** {vault.name}\n\n"
            f"💡 Use `/tag {file_record_id} work,important` to organize",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup,
        )

    except Exception as e:
        error_msg = str(e)
        if "chat not found" in error_msg.lower():
            error_msg = (
                "The vault group chat was not found. "
                "Make sure I'm added as admin to the group."
            )
        elif "bot was kicked" in error_msg.lower():
            error_msg = (
                "I was removed from the vault group. "
                "Please add me back as admin and use `/setvault` again."
            )
        await status_msg.edit_text(
            f"❌ **Error saving file:**\n`{error_msg}`",
            parse_mode=ParseMode.MARKDOWN
        )