"""
FileVault Bot — Main Entry Point
Multi-tier Telegram bot with PostgreSQL backend.
Structured admin panel with approval flow and audit logging.
"""
import os
import asyncio
import logging

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes,
)

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Bot token — must be set in environment
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is not set!")

# Import command handlers
from commands.core import (
    start_command, help_command, status_command, whoami_command,
)
from commands.organize import (
    list_command, search_command, tag_command, delete_command, rename_command,
)
from commands.admin import (
    admin_command, admin_users_command, admin_approve_command, admin_deny_command,
    admin_stats_command, admin_logs_command, admin_broadcast_command,
    admin_setrole_command, admin_vault_command,
    settings_command, setnotif_command, setvault_command,
    adduser_command, removeuser_command,
)
from handlers import handle_file
from callbacks import handle_callback
from database import init_db, close_db


async def main():
    """Initialize database, create application, and start the bot."""
    logger.info("Initializing database...")
    await init_db()

    logger.info("Creating bot application...")
    app = Application.builder().token(BOT_TOKEN).build()

    # ===== Tier 1: Core Commands =====
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("whoami", whoami_command))

    # ===== Tier 2: Retrieval & Organization =====
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("tag", tag_command))
    app.add_handler(CommandHandler("delete", delete_command))
    app.add_handler(CommandHandler("rename", rename_command))

    # ===== Tier 3: Admin & Settings =====
    # Admin panel commands
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("admin_users", admin_users_command))
    app.add_handler(CommandHandler("admin_approve", admin_approve_command))
    app.add_handler(CommandHandler("admin_deny", admin_deny_command))
    app.add_handler(CommandHandler("admin_stats", admin_stats_command))
    app.add_handler(CommandHandler("admin_logs", admin_logs_command))
    app.add_handler(CommandHandler("admin_broadcast", admin_broadcast_command))
    app.add_handler(CommandHandler("admin_setrole", admin_setrole_command))
    app.add_handler(CommandHandler("admin_vault", admin_vault_command))

    # Legacy admin commands
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("setnotif", setnotif_command))
    app.add_handler(CommandHandler("setvault", setvault_command))
    app.add_handler(CommandHandler("adduser", adduser_command))
    app.add_handler(CommandHandler("removeuser", removeuser_command))

    # ===== File Handler =====
    app.add_handler(MessageHandler(
        filters.Document.ALL | filters.PHOTO | filters.VIDEO |
        filters.AUDIO | filters.VOICE | filters.ANIMATION,
        handle_file
    ))

    # ===== Inline Callback Handler =====
    app.add_handler(CallbackQueryHandler(handle_callback))

    # ===== Error Handler =====
    async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Exception while handling update: {context.error}", exc_info=context.error)

    app.add_error_handler(error_handler)

    # Set up graceful shutdown
    app.post_shutdown = close_db

    logger.info("🤖 Bot is running!")
    logger.info("📋 Registered commands: 25")
    logger.info("🗄️  Database: PostgreSQL via Supabase")

    # Start polling — use initialize + start + idle pattern for async context
    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)

    # Keep running until interrupted
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutting down...")
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
