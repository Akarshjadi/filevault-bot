"""
Tier 1 — Core Commands
Onboarding, help, status, whoami
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from sqlalchemy import select, text

from utils import ensure_user, DbSession, format_file_size
from models import Vault


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Onboarding message — explains what the bot does, shows welcome menu."""
    if not await ensure_user(update, context):
        return

    user = update.effective_user

    keyboard = [
        [
            InlineKeyboardButton("📤 How to Save", callback_data="howto_save"),
            InlineKeyboardButton("⚙️ Settings", callback_data="settings"),
        ],
        [
            InlineKeyboardButton("📁 Recent Files", callback_data="list_recent"),
            InlineKeyboardButton("📖 Full Help", callback_data="help"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    welcome_text = (
        f"👋 Welcome to **FileVault Bot**, @{user.username or user.first_name}!\n\n"
        f"📦 **What I do:** I store and organize your files securely.\n"
        f"Just send me any file and I'll save it in your private vault.\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"**Quick Start:**\n"
        f"1️⃣ Send any file (photo, video, document, audio)\n"
        f"2️⃣ I'll save it and give you an ID\n"
        f"3️⃣ Use commands to search, tag, or delete\n\n"
        f"**Core Commands:**\n"
        f"• `/help` — Full command list\n"
        f"• `/status` — Check vault & storage\n"
        f"• `/list` — Browse recent files\n"
        f"• `/search <kw>` — Search files\n"
        f"• `/delete <id>` — Remove a file\n\n"
        f"💡 _Tip: Use the buttons below to explore!_"
    )

    await update.message.reply_text(
        welcome_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Full command list with examples — grouped by tier."""
    if not await ensure_user(update, context):
        return

    help_text = (
        "📖 **Command Reference**\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "**📤 FILE STORAGE**\n"
        "Send or forward any file (document, photo, video, audio) "
        "and I'll store it automatically in your vault group.\n"
        "\n"
        "**📋 FILE MANAGEMENT**\n"
        "`/list [page]` — View recent files (10 per page)\n"
        "  _Example:_ `/list` or `/list 2`\n"
        "\n"
        "`/search <keyword>` — Search by filename, caption, or tags\n"
        "  _Example:_ `/search report`\n"
        "\n"
        "`/tag <file_id> <tag1,tag2>` — Add tags to a file\n"
        "  _Example:_ `/tag 5 work,urgent`\n"
        "\n"
        "`/rename <file_id> <new_name>` — Rename a file reference\n"
        "  _Example:_ `/rename 5 AnnualReport.pdf`\n"
        "\n"
        "`/delete <file_id>` — Permanently remove from vault\n"
        "  _Example:_ `/delete 5`\n"
        "\n"
        "**⚙️ SETTINGS & ACCOUNT**\n"
        "`/settings` — View & modify preferences\n"
        "`/status` — Check vault connection and storage\n"
        "`/whoami` — Show your user ID and permission level\n"
        "`/setnotif <on/off>` — Toggle notifications\n"
        "\n"
        "**🛡️ ADMIN COMMANDS**\n"
        "`/setvault` — Bind current group as vault (run in group)\n"
        "`/adduser <user_id>` — Add user to whitelist\n"
        "`/removeuser <user_id>` — Remove user from whitelist\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 Need help? Contact your bot administrator."
    )

    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show vault connection status, file count, storage used."""
    if not await ensure_user(update, context):
        return

    user = update.effective_user
    async with DbSession() as session:
        result = await session.execute(
            select(Vault).where(Vault.owner_user_id == user.id)
        )
        vault = result.scalar_one_or_none()

        result = await session.execute(
            text("SELECT COUNT(*), COALESCE(SUM(file_size), 0) FROM files WHERE sender_user_id = :uid"),
            {"uid": user.id}
        )
        file_count, total_size = result.fetchone()

    vault_status = "✅ Connected" if vault else "⚠️ Not bound"
    vault_id_display = f"`{vault.telegram_group_id}`" if vault else "None (use /setvault in group)"

    status_text = (
        f"🔍 **Bot Status Report**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🗄️ **Vault Status:** {vault_status}\n"
        f"📁 **Files Saved:** {file_count}\n"
        f"💾 **Storage Used:** {format_file_size(total_size)}\n"
        f"🆔 **Vault Group ID:** {vault_id_display}\n"
        f"🤖 **Bot Status:** ✅ Online\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
    )

    await update.message.reply_text(status_text, parse_mode=ParseMode.MARKDOWN)


async def whoami_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's permission level, ID, and username."""
    if not await ensure_user(update, context):
        return

    user = update.effective_user
    async with DbSession() as session:
        result = await session.execute(
            text("SELECT role FROM users WHERE user_id = :uid"),
            {"uid": user.id}
        )
        row = result.fetchone()
        role = row[0] if row else "whitelisted"

    whoami_text = (
        f"👤 **User Information**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 **User ID:** `{user.id}`\n"
        f"👤 **Username:** @{user.username or 'N/A'}\n"
        f"📛 **Name:** {user.first_name or 'N/A'}\n"
        f"🔑 **Permission Level:** `{role.upper()}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
    )

    await update.message.reply_text(whoami_text, parse_mode=ParseMode.MARKDOWN)
