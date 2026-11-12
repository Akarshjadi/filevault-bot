"""
Tier 2 — Retrieval & Organization Commands
list, search, tag, rename, delete
"""
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from sqlalchemy import text, select

from utils import ensure_user, DbSession, format_file_size, log_audit, escape_markdown
from models import File, Vault


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List recent files, paginated (10 per page)."""
    if not await ensure_user(update, context):
        return

    page = 1
    if context.args and len(context.args) > 0:
        try:
            page = max(1, int(context.args[0]))
        except ValueError:
            page = 1

    user = update.effective_user
    per_page = 10
    offset = (page - 1) * per_page

    async with DbSession() as session:
        # Get total count for this user
        result = await session.execute(
            text("SELECT COUNT(*) FROM files WHERE sender_user_id = :uid"),
            {"uid": user.id}
        )
        total = result.scalar()

        # Get paginated files
        result = await session.execute(
            text("""
                SELECT f.file_id_pk, f.file_name, f.tags, f.file_type,
                       f.saved_at, f.file_size
                FROM files f
                WHERE f.sender_user_id = :uid
                ORDER BY f.saved_at DESC
                LIMIT :limit OFFSET :offset
            """),
            {"uid": user.id, "limit": per_page, "offset": offset}
        )
        files = result.fetchall()

    if not files:
        await update.message.reply_text(
            "📭 No files in your vault yet.\n\nSend me a file to get started!"
        )
        return

    response = f"📂 **Recent Files** (Page {page})\n━━━━━━━━━━━━━━━━━━━━━\n"

    for file in files:
        file_id, file_name, tags, file_type, saved_at, size = file
        tag_str = ""
        safe_file_name = escape_markdown(file_name or "Unknown")
        if tags and len(tags) > 0:
            safe_tags = [escape_markdown(t) for t in tags[:3]]
            tag_str = f" 🏷️ `{', '.join(safe_tags)}`"
        response += (
            f"`{file_id}.` **{safe_file_name}**\n"
            f"   📁 {file_type} | 💾 {format_file_size(size or 0)}\n"
            f"   🆔 `{file_id}` | 🕐 {str(saved_at)[:10]}{tag_str}\n\n"
        )

    total_pages = max(1, (total + per_page - 1) // per_page)
    response += f"Page {page}/{total_pages} | Total: {total} files"

    await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search files by filename, caption, or tags."""
    if not await ensure_user(update, context):
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ Usage: `/search <keyword>`\nExample: `/search report`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    keyword = ' '.join(context.args)
    safe_keyword = escape_markdown(keyword)
    keyword_lower = keyword.lower()
    user = update.effective_user

    async with DbSession() as session:
        result = await session.execute(
            text("""
                SELECT f.file_id_pk, f.file_name, f.tags, f.file_type,
                       f.saved_at, f.caption
                FROM files f
                WHERE f.sender_user_id = :uid
                  AND (
                    LOWER(f.file_name) LIKE :kw
                    OR LOWER(COALESCE(f.caption, '')) LIKE :kw
                  )
                ORDER BY f.saved_at DESC
                LIMIT 10
            """),
            {"uid": user.id, "kw": f"%{keyword_lower}%"}
        )
        files = result.fetchall()

    if not files:
        await update.message.reply_text(
            f"🔍 No files found matching: **{safe_keyword}**",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    response = f"🔍 **Search Results** for \"{safe_keyword}\"\n━━━━━━━━━━━━━━━━━━━━━\n"

    for file in files:
        file_id, file_name, tags, file_type, saved_at, caption = file
        safe_file_name = escape_markdown(file_name or "Unknown")
        response += f"`{file_id}.` **{safe_file_name}**\n"
        response += f"   📁 {file_type} | 🕐 {str(saved_at)[:10]}\n"
        if tags and len(tags) > 0:
            safe_tags = [escape_markdown(t) for t in tags[:5]]
            response += f"   🏷️ Tags: `{', '.join(safe_tags)}`\n"
        if caption:
            caption_preview = caption[:80] + "..." if len(caption) > 80 else caption
            safe_caption = escape_markdown(caption_preview)
            response += f"   💬 {safe_caption}\n"
        response += "\n"

    await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)


async def tag_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add tags to a saved file."""
    if not await ensure_user(update, context):
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "⚠️ Usage: `/tag <file_id> <tag1,tag2,...>`\n"
            "Example: `/tag 5 work,urgent,2024`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        file_id_pk = int(context.args[0])
        new_tags = [t.strip() for t in context.args[1].split(',') if t.strip()]
    except ValueError:
        await update.message.reply_text("⚠️ Invalid file ID. Must be a number.")
        return

    user = update.effective_user

    async with DbSession() as session:
        result = await session.execute(
            text("SELECT tags FROM files WHERE file_id_pk = :fid AND sender_user_id = :uid"),
            {"fid": file_id_pk, "uid": user.id}
        )
        row = result.fetchone()

        if not row:
            await update.message.reply_text(
                f"❌ File with ID {file_id_pk} not found or not yours."
            )
            return

        current_tags = list(row[0]) if row[0] else []
        # Merge and deduplicate
        updated_tags = list(set(current_tags + new_tags))

        await session.execute(
            text("UPDATE files SET tags = :tags WHERE file_id_pk = :fid"),
            {"tags": updated_tags, "fid": file_id_pk}
        )

        # Log audit
        await log_audit(
            user_id=user.id,
            action="tag",
            file_id=file_id_pk,
            details=f"Added tags: {', '.join(new_tags)}",
            session=session,
        )

    safe_tags = [escape_markdown(t) for t in updated_tags]
    await update.message.reply_text(
        f"✅ Tags updated for file {file_id_pk}.\n"
        f"🏷️ Current tags: `{', '.join(safe_tags)}`",
        parse_mode=ParseMode.MARKDOWN
    )


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove file from vault + try to delete message from vault group."""
    if not await ensure_user(update, context):
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ Usage: `/delete <file_id>`\nExample: `/delete 5`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        file_id_pk = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ Invalid file ID. Must be a number.")
        return

    user = update.effective_user

    async with DbSession() as session:
        result = await session.execute(
            text("""
                SELECT file_name, vault_message_id, vault_id
                FROM files
                WHERE file_id_pk = :fid AND sender_user_id = :uid
            """),
            {"fid": file_id_pk, "uid": user.id}
        )
        row = result.fetchone()

        if not row:
            await update.message.reply_text(
                f"❌ File with ID {file_id_pk} not found or not yours."
            )
            return

        file_name, vault_message_id, vault_id = row

        # Try to delete the message from the vault group
        if vault_message_id and vault_id:
            try:
                vault_result = await session.execute(
                    text("SELECT telegram_group_id FROM vaults WHERE vault_id = :vid"),
                    {"vid": vault_id}
                )
                vault_row = vault_result.fetchone()
                if vault_row:
                    await context.bot.delete_message(
                        chat_id=vault_row[0],
                        message_id=vault_message_id
                    )
            except Exception:
                pass  # Ignore if message already deleted or bot can't

        # Log audit
        await log_audit(
            user_id=user.id,
            action="delete",
            file_id=file_id_pk,
            details=f"Deleted file: {file_name}",
            session=session,
        )

        # Delete from database
        await session.execute(
            text("DELETE FROM files WHERE file_id_pk = :fid"),
            {"fid": file_id_pk}
        )

    safe_file_name = escape_markdown(file_name or "Unknown")
    await update.message.reply_text(
        f"🗑️ File **{safe_file_name}** (ID: {file_id_pk}) has been permanently deleted.",
        parse_mode=ParseMode.MARKDOWN
    )


async def rename_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Rename a stored file reference."""
    if not await ensure_user(update, context):
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "⚠️ Usage: `/rename <file_id> <new_name>`\n"
            "Example: `/rename 5 Report2024.pdf`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        file_id_pk = int(context.args[0])
        new_name = ' '.join(context.args[1:])
    except ValueError:
        await update.message.reply_text("⚠️ Invalid file ID. Must be a number.")
        return

    user = update.effective_user

    async with DbSession() as session:
        result = await session.execute(
            text("UPDATE files SET file_name = :name WHERE file_id_pk = :fid AND sender_user_id = :uid"),
            {"name": new_name, "fid": file_id_pk, "uid": user.id}
        )

        if result.rowcount == 0:
            await update.message.reply_text(
                f"❌ File with ID {file_id_pk} not found or not yours."
            )
            return

        # Log audit
        await log_audit(
            user_id=user.id,
            action="rename",
            file_id=file_id_pk,
            details=f"Renamed to: {new_name}",
            session=session,
        )

    safe_new_name = escape_markdown(new_name)
    await update.message.reply_text(
        f"✅ File renamed to: **{safe_new_name}**",
        parse_mode=ParseMode.MARKDOWN
    )