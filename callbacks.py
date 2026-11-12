"""
Inline callback handlers — DPDP Act 2023 Compliant
Adult/Minor approval flows with pseudonymization.
"""
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from sqlalchemy import select, text

from utils import DbSession, escape_markdown, log_audit
from handlers import get_shared_vault
from models import (
    Vault, File, FileType, PendingFile, User, UserRole,
    Incident, Person, IncidentPerson
)
from storage import upload_file, delete_file
from crypto_utils import hash_person_name, encrypt_field, is_minor_by_name


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard button presses."""
    query = update.callback_query
    await query.answer()

    data = query.data
    user = query.from_user

    if data == "howto_save":
        await query.edit_message_text(
            "📤 **How to Save Files**\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "1️⃣ Make sure a vault group is set up (ask admin)\n"
            "2️⃣ Open a DM with me\n"
            "3️⃣ Send any file (photo, video, document, audio)\n"
            "4️⃣ I'll send it for admin review\n"
            "5️⃣ Once approved, your file is saved in your vault\n\n"
            "**Tip:** You can also forward files from other chats!",
            parse_mode=ParseMode.MARKDOWN
        )

    elif data == "settings":
        await query.edit_message_text(
            "⚙️ **Settings**\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "• `/setnotif on|off` — Toggle notifications\n"
            "• `/whoami` — View your account info\n"
            "• `/status` — Check vault & storage\n\n"
            "More settings coming soon!",
            parse_mode=ParseMode.MARKDOWN
        )

    elif data == "list_recent":
        await query.edit_message_text(
            "📂 **Recent Files**\n"
            "Use `/list` to browse your saved files.\n"
            "Example: `/list` or `/list 2`",
            parse_mode=ParseMode.MARKDOWN
        )

    elif data == "help":
        await query.edit_message_text(
            "📖 **Help**\n"
            "Use `/help` to see the full command list.\n\n"
            "**Quick Reference:**\n"
            "`/list` — Browse files\n"
            "`/search <kw>` — Search\n"
            "`/tag <id> <tags>` — Add tags\n"
            "`/delete <id>` — Remove file\n"
            "`/status` — Check vault",
            parse_mode=ParseMode.MARKDOWN
        )

    elif data.startswith("approve_adult_") or data.startswith("approve_minor_"):
        await handle_approval(query, context, data)

    elif data.startswith("reject_"):
        await handle_rejection(query, context, data)


async def handle_approval(query, context: ContextTypes.DEFAULT_TYPE, data: str):
    """Handle adult/minor approval with person extraction."""
    is_minor = data.startswith("approve_minor_")
    pending_id = int(data.split("_")[-1])
    admin = query.from_user

    async with DbSession() as session:
        # Get pending file
        result = await session.execute(
            select(PendingFile).where(PendingFile.pending_id == pending_id)
        )
        pending = result.scalar_one_or_none()

        if not pending or pending.status != "pending":
            await query.edit_message_text(
                "⚠️ This file was already reviewed.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # Get incident
        incident = await session.get(Incident, pending.incident_id)
        if not incident:
            await query.edit_message_text(
                "❌ Incident record not found.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # Store approval state for next message
        context.user_data["approval_state"] = {
            "pending_id": pending_id,
            "is_minor": is_minor,
            "incident_id": incident.incident_id,
            "admin_id": admin.id,
        }

        # Prompt for person name
        await query.edit_message_text(
            f"✅ **{'⚠️ MINOR' if is_minor else 'Adult'} file approved**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Incident ID: `{incident.incident_id}`\n"
            f"File: {pending.file_name}\n\n"
            f"**Reply with the person's name** (or `/skip` if unknown).\n"
            f"Names are encrypted and never stored in plaintext.",
            parse_mode=ParseMode.MARKDOWN
        )


async def handle_rejection(query, context: ContextTypes.DEFAULT_TYPE, data: str):
    """Handle file rejection."""
    pending_id = int(data.split("_")[-1])
    admin = query.from_user

    async with DbSession() as session:
        result = await session.execute(
            select(PendingFile).where(PendingFile.pending_id == pending_id)
        )
        pending = result.scalar_one_or_none()

        if not pending or pending.status != "pending":
            await query.edit_message_text(
                "⚠️ This file was already reviewed.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # Update pending status
        pending.status = "rejected"
        pending.reviewed_by = admin.id
        pending.reviewed_at = datetime.now(timezone.utc)

        # Delete from staging channel
        if pending.staging_message_id:
            try:
                await context.bot.delete_message(
                    chat_id=context.bot.id,
                    message_id=pending.staging_message_id
                )
            except Exception:
                pass

        await log_audit(
            user_id=admin.id,
            action="reject_file",
            details=f"Rejected file {pending.file_name}",
            session=session,
        )

    # Notify user
    try:
        safe_file_name = escape_markdown(pending.file_name or "Unknown")
        await context.bot.send_message(
            chat_id=pending.sender_user_id,
            text=(
                f"❌ **Your file has been rejected.**\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📄 **File:** {safe_file_name}\n\n"
                f"An administrator has rejected your file submission."
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass

    await query.edit_message_text(
        f"❌ **File Rejected**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"The user has been notified.",
        parse_mode=ParseMode.MARKDOWN
    )


async def process_person_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process admin's reply with person name after approval."""
    state = context.user_data.get("approval_state")
    if not state:
        return

    name = update.message.text.strip()
    if name == "/skip":
        name = f"unknown_{state['pending_id']}"

    is_minor = state["is_minor"]
    
    # Hash and encrypt name
    name_hash = hash_person_name(name)
    encrypted_name, nonce = encrypt_field(name)

    async with DbSession() as session:
        # Get or create person
        result = await session.execute(
            select(Person).where(Person.person_hash == name_hash)
        )
        person = result.scalar_one_or_none()

        if not person:
            person = Person(
                person_hash=name_hash,
                encrypted_name=encrypted_name,
                name_nonce=nonce,
                is_minor=is_minor,
            )
            session.add(person)
            await session.flush()
        else:
            # Update minor flag if needed
            if is_minor and not person.is_minor:
                person.is_minor = True
                person.encrypted_name = encrypted_name
                person.name_nonce = nonce

        # Link person to incident
        link = IncidentPerson(
            incident_id=state["incident_id"],
            person_id=person.person_id,
        )
        session.add(link)

        # Get pending file
        pending = await session.get(PendingFile, state["pending_id"])

        # Upload to R2
        cloud_key = None
        if pending.staging_message_id:
            try:
                # Download from staging
                staging_msg = await context.bot.forward_message(
                    chat_id=update.effective_chat.id,
                    from_chat_id=context.bot.id,
                    message_id=pending.staging_message_id
                )
                
                file_obj = staging_msg.document or staging_msg.photo[-1] if staging_msg.photo else None
                if file_obj:
                    tg_file = await file_obj.get_file()
                    file_bytes = await tg_file.download_as_bytearray()
                    
                    # Determine cloud key path
                    bucket_prefix = "minors-encrypted" if is_minor else "approved"
                    cloud_key = f"{bucket_prefix}/{pending.telegram_file_unique_id}/{pending.file_name}"
                    
                    # Upload to R2
                    upload_file(bytes(file_bytes), cloud_key, is_minor=is_minor)
                    
                    # Delete from staging
                    await context.bot.delete_message(
                        chat_id=context.bot.id,
                        message_id=pending.staging_message_id
                    )
            except Exception as e:
                await update.message.reply_text(
                    f"⚠️ R2 upload failed: {e}\nFile saved without cloud backup.",
                    parse_mode=ParseMode.MARKDOWN
                )

        # Create file record
        file_record = File(
            incident_id=state["incident_id"],
            person_id=person.person_id,
            telegram_file_id=pending.telegram_file_id,
            telegram_file_unique_id=pending.telegram_file_unique_id,
            cloud_key=cloud_key or "local_only",
            vault_message_id=0,
            file_type=pending.file_type,
            file_size=pending.file_size,
            original_filename=pending.file_name,
            caption=pending.caption,
            is_minor=is_minor,
        )
        session.add(file_record)

        # Update pending status
        pending.status = "approved"
        pending.reviewed_by = state["admin_id"]
        pending.reviewed_at = datetime.now(timezone.utc)
        pending.cloud_key = cloud_key
        pending.temp_person_name = name  # Temporary for review, can be purged later

        await log_audit(
            user_id=state["admin_id"],
            action="approve_file",
            details=f"Approved {'minor' if is_minor else 'adult'} file: {pending.file_name}",
            session=session,
        )

        await session.commit()

    # Notify user
    try:
        safe_file_name = escape_markdown(pending.file_name or "Unknown")
        await context.bot.send_message(
            chat_id=pending.sender_user_id,
            text=(
                f"✅ **Your file has been approved!**\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📄 **File:** {safe_file_name}\n\n"
                f"Your file is now in the vault."
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass

    await update.message.reply_text(
        f"✅ **File processed!**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Person hash: `{name_hash[:16]}...`\n"
        f"Encrypted name stored.\n"
        f"Bucket: `{'minors-encrypted' if is_minor else 'approved'}`",
        parse_mode=ParseMode.MARKDOWN
    )

    # Clear state
    context.user_data.pop("approval_state", None)