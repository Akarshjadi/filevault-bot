"""
File Handler — Core save flow with admin approval gate.
When a user sends a file, it goes to ALL admins for review first.
Only after admin approval is the file stored in the vault.
"""
import os
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from sqlalchemy import select, text

from utils import ensure_user, DbSession, map_telegram_file_type, format_file_size, log_audit, escape_markdown
from models import Vault, File, FileType, PendingFile, User, UserRole

# Storage path for local file copies (optional fallback)
STORAGE_BASE = os.getenv("STORAGE_BASE", "./vault_storage")


async def get_admin_ids() -> list[int]:
    """Get all admin user IDs."""
    async with DbSession() as session:
        result = await session.execute(
            select(User.user_id).where(User.role == UserRole.ADMIN)
        )
        return [row[0] for row in result.fetchall()]


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming files — send to admin for review, not directly to vault."""
    if not await ensure_user(update, context):
        return

    user = update.effective_user

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
    status_msg = await update.message.reply_text("📥 Sending for admin review...")

    try:
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

        # Map to our FileType enum
        mapped_type = map_telegram_file_type(file_type_str)
        original_caption = update.message.caption or ""

        # Save as pending file in database
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
                status="pending",
            )
            session.add(pending)
            await session.flush()
            pending_id = pending.pending_id

            # Log audit
            await log_audit(
                user_id=user.id,
                action="file_pending_approval",
                details=f"File submitted for approval: {file_name} ({file_type_str})",
                session=session,
            )

        # Build review message for admins
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
            f"💾 **Size:** {format_file_size(file_obj.file_size or 0)}\n"
            f"🔖 **Pending ID:** `{pending_id}`\n"
        )
        if safe_caption:
            review_caption += f"💬 **Caption:** {safe_caption}\n"

        # Approve/Reject buttons
        keyboard = [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve_file_{pending_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_file_{pending_id}"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Send the file to ALL admins for review
        admin_ids = await get_admin_ids()
        sent_to_admins = 0
        for admin_id in admin_ids:
            try:
                await context.bot.copy_message(
                    chat_id=admin_id,
                    from_chat_id=update.effective_chat.id,
                    message_id=update.message.message_id,
                    caption=review_caption,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=reply_markup,
                )
                sent_to_admins += 1
            except Exception:
                pass  # Admin may have blocked the bot

        if sent_to_admins == 0:
            # No admins available — notify user
            await status_msg.edit_text(
                "⚠️ **No admins are available to review your file.**\n\n"
                "Please try again later or contact the bot administrator.",
                parse_mode=ParseMode.MARKDOWN
            )
            # Clean up pending record
            async with DbSession() as session:
                await session.execute(
                    text("DELETE FROM pending_files WHERE pending_id = :pid"),
                    {"pid": pending_id}
                )
            return

        # Notify the user
        await status_msg.edit_text(
            f"✅ **File sent for admin review!**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📄 **File:** {safe_file_name}\n"
            f"🔖 **Request ID:** `{pending_id}`\n\n"
            f"⏳ An admin will review your file shortly.\n"
            f"You'll be notified when it's approved or rejected.",
            parse_mode=ParseMode.MARKDOWN
        )

    except Exception as e:
        error_msg = str(e)
        await status_msg.edit_text(
            f"❌ **Error submitting file:**\n`{error_msg}`",
            parse_mode=ParseMode.MARKDOWN
        )


async def approve_pending_file(update: Update, context: ContextTypes.DEFAULT_TYPE, pending_id: int):
    """Approve a pending file — copy it to the user's vault."""
    admin = update.effective_user
    query = update.callback_query

    async with DbSession() as session:
        # Get pending file record
        result = await session.execute(
            select(PendingFile).where(PendingFile.pending_id == pending_id)
        )
        pending = result.scalar_one_or_none()

        if not pending:
            await query.edit_message_text(
                "❌ This file request no longer exists.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        if pending.status != "pending":
            await query.edit_message_text(
                f"⚠️ This file was already **{pending.status}**.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # Find user's vault
        result = await session.execute(
            select(Vault).where(Vault.owner_user_id == pending.sender_user_id)
        )
        vault = result.scalar_one_or_none()

        if not vault:
            await query.edit_message_text(
                "⚠️ **User has no vault configured!**\n\n"
                "An admin needs to set up a vault for this user first using `/setvault`.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # Build caption for vault
        safe_file_name = escape_markdown(pending.file_name or "Unknown")
        meta_caption = (
            f"📤 **User {pending.sender_user_id}**\n"
            f"📁 `{pending.file_type.value}` | 💾 {format_file_size(pending.file_size or 0)}"
        )
        if pending.caption:
            safe_caption = escape_markdown(pending.caption)
            meta_caption += f"\n💬 {safe_caption}"
        if pending.file_name:
            meta_caption = f"📄 `{safe_file_name}`\n" + meta_caption

        # Copy the file to the vault group
        try:
            copied_msg = await context.bot.copy_message(
                chat_id=vault.telegram_group_id,
                from_chat_id=pending.chat_id,
                message_id=pending.message_id,
                caption=meta_caption,
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            await query.edit_message_text(
                f"❌ **Failed to copy file to vault:**\n`{str(e)}`",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # Create file record
        file_record = File(
            telegram_file_id=pending.telegram_file_id,
            telegram_file_unique_id=pending.telegram_file_unique_id,
            vault_message_id=copied_msg.message_id,
            vault_id=vault.vault_id,
            sender_user_id=pending.sender_user_id,
            file_type=pending.file_type,
            file_size=pending.file_size,
            file_name=pending.file_name,
            caption=pending.caption,
            tags=[],
            topic_id=None,
        )
        session.add(file_record)
        await session.flush()
        file_record_id = file_record.file_id_pk

        # Update pending record
        pending.status = "approved"
        pending.reviewed_by = admin.id
        pending.reviewed_at = datetime.now()

        # Log audit
        await log_audit(
            user_id=admin.id,
            action="approve_file",
            file_id=file_record_id,
            details=f"Approved file {pending.file_name} from user {pending.sender_user_id}",
            session=session,
        )

    # Notify the user who submitted the file
    try:
        safe_file_name = escape_markdown(pending.file_name or "Unknown")
        await context.bot.send_message(
            chat_id=pending.sender_user_id,
            text=(
                f"✅ **Your file has been approved!**\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📄 **File:** {safe_file_name}\n"
                f"🔗 **ID:** `{file_record_id}`\n\n"
                f"Your file is now stored in your vault.\n"
                f"Use `/list` to browse your files.",
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass  # User may have blocked the bot

    # Update the admin's review message
    await query.edit_message_text(
        f"✅ **File Approved!**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📄 **File:** {safe_file_name}\n"
        f"👤 **User:** `{pending.sender_user_id}`\n"
        f"🔗 **File ID:** `{file_record_id}`\n\n"
        f"The file has been saved to the vault and the user has been notified.",
        parse_mode=ParseMode.MARKDOWN
    )


async def reject_pending_file(update: Update, context: ContextTypes.DEFAULT_TYPE, pending_id: int):
    """Reject a pending file — notify the user."""
    admin = update.effective_user
    query = update.callback_query

    async with DbSession() as session:
        # Get pending file record
        result = await session.execute(
            select(PendingFile).where(PendingFile.pending_id == pending_id)
        )
        pending = result.scalar_one_or_none()

        if not pending:
            await query.edit_message_text(
                "❌ This file request no longer exists.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        if pending.status != "pending":
            await query.edit_message_text(
                f"⚠️ This file was already **{pending.status}**.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # Update pending record
        pending.status = "rejected"
        pending.reviewed_by = admin.id
        pending.reviewed_at = datetime.now()

        # Log audit
        await log_audit(
            user_id=admin.id,
            action="reject_file",
            details=f"Rejected file {pending.file_name} from user {pending.sender_user_id}",
            session=session,
        )

    # Notify the user who submitted the file
    try:
        safe_file_name = escape_markdown(pending.file_name or "Unknown")
        await context.bot.send_message(
            chat_id=pending.sender_user_id,
            text=(
                f"❌ **Your file has been rejected.**\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📄 **File:** {safe_file_name}\n\n"
                f"An administrator has rejected your file submission.\n"
                f"Please contact an admin if you believe this is an error.",
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass  # User may have blocked the bot

    # Update the admin's review message
    safe_file_name = escape_markdown(pending.file_name or "Unknown")
    await query.edit_message_text(
        f"❌ **File Rejected**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📄 **File:** {safe_file_name}\n"
        f"👤 **User:** `{pending.sender_user_id}`\n\n"
        f"The user has been notified.",
        parse_mode=ParseMode.MARKDOWN
    )