"""
Inline callback handlers for keyboard buttons (Tag, Delete, etc.)
"""
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from sqlalchemy import text

from utils import DbSession


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
            "4️⃣ I'll save it and give you a file ID\n"
            "5️⃣ Use commands like `/tag`, `/list`, `/search`\n\n"
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
        # Delegate to list command logic
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

    elif data.startswith("tag_"):
        file_id = data.split("_")[1]
        await query.edit_message_text(
            f"🏷️ **Tag File {file_id}**\n"
            f"Use: `/tag {file_id} tag1,tag2`\n"
            f"Example: `/tag {file_id} work,important`",
            parse_mode=ParseMode.MARKDOWN
        )

    elif data.startswith("delete_"):
        file_id = data.split("_")[1]
        await query.edit_message_text(
            f"🗑️ **Delete File {file_id}**\n"
            f"Use: `/delete {file_id}`\n"
            f"This will permanently remove the file.",
            parse_mode=ParseMode.MARKDOWN
        )