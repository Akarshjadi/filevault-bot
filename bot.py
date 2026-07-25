"""
FileVault Bot — DPDP Act 2023 Compliant
Main entry point with migration runner and handler registration.
"""
import asyncio
import sys
from pathlib import Path

from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters

from utils import ensure_user, DbSession, log_audit
from database import init_db, async_engine
from models import Base, User, UserRole
from commands.core import start_command, help_command, about_command, my_data_command, forget_command
from commands.admin import admin_command, admin_users_command, admin_approve_command, admin_deny_command, admin_stats_command, admin_logs_command, admin_broadcast_command, admin_setrole_command, admin_vault_command, settings_command, setnotif_command, setvault_command, adduser_command, removeuser_command
from handlers import handle_file
from callbacks import handle_callback, process_person_name

# Import new evidence vault handlers
from handlers.register import get_registration_handler
from handlers.submit import get_submission_handler
from handlers.consent import get_consent_handlers
from handlers.admin import get_admin_handler, set_admin_ids
from handlers.forget import get_forget_handler, get_revoke_consent_handler

# Import task queue
from tasks import start_background_workers, stop_background_workers, enqueue_processing_job


BOT_TOKEN = sys.argv[1] if len(sys.argv) > 1 else None
if not BOT_TOKEN:
    from dotenv import load_dotenv
    import os
    load_dotenv()
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN not provided. Pass as argument or set in .env")
        sys.exit(1)


async def run_migrations():
    """Run pending database migrations on startup."""
    from sqlalchemy import text
    from sqlalchemy.exc import ProgrammingError
    
    migrations_dir = Path(__file__).parent / "migrations"
    
    async with async_engine.begin() as conn:
        # Create migrations tracking table
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version VARCHAR(50) PRIMARY KEY,
                applied_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        
        # Get applied migrations
        result = await conn.execute(text("SELECT version FROM schema_migrations"))
        applied = {row[0] for row in result.fetchall()}
        
        # Apply pending migrations
        migration_files = sorted(migrations_dir.glob("*.sql"))
        for migration_file in migration_files:
            version = migration_file.stem
            if version not in applied:
                print(f"Applying migration: {version}")
                sql = migration_file.read_text()
                try:
                    await conn.execute(text(sql))
                    await conn.execute(
                        text("INSERT INTO schema_migrations (version) VALUES (:v)"),
                        {"v": version}
                    )
                    print(f"  ✓ Applied {version}")
                except Exception as e:
                    print(f"  ✗ Failed {version}: {e}")
        
        # Ensure encryption_keys has a master salt
        result = await conn.execute(
            text("SELECT COUNT(*) FROM encryption_keys WHERE key_name = 'master_salt'")
        )
        if result.scalar() == 0:
            print("WARNING: master_salt not found in encryption_keys. Set ENCRYPTION_MASTER_SALT in .env")
        
        print("Migrations complete")


async def main():
    # Run migrations
    await run_migrations()
    
    # Build application
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Register evidence vault handlers
    application.add_handler(get_registration_handler())
    application.add_handler(get_submission_handler())
    application.add_handler(get_forget_handler())
    application.add_handler(get_revoke_consent_handler())
    
    # Register admin handler with admin IDs from environment
    import os
    admin_ids_str = os.getenv("ADMIN_IDS", "")
    if admin_ids_str:
        try:
            admin_ids = [int(id.strip()) for id in admin_ids_str.split(",") if id.strip()]
            set_admin_ids(admin_ids)
            print(f"Loaded {len(admin_ids)} admin IDs")
        except ValueError as e:
            print(f"WARNING: Invalid ADMIN_IDS format: {e}")
    
    application.add_handler(get_admin_handler())
    
    # Register consent handlers
    for handler in get_consent_handlers():
        application.add_handler(handler)
    
    # Existing handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("about", about_command))
    application.add_handler(CommandHandler("my_data", my_data_command))
    application.add_handler(CommandHandler("forget", forget_command))
    
    # Admin handlers
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("admin_users", admin_users_command))
    application.add_handler(CommandHandler("admin_approve", admin_approve_command))
    application.add_handler(CommandHandler("admin_deny", admin_deny_command))
    application.add_handler(CommandHandler("admin_stats", admin_stats_command))
    application.add_handler(CommandHandler("admin_logs", admin_logs_command))
    application.add_handler(CommandHandler("admin_broadcast", admin_broadcast_command))
    application.add_handler(CommandHandler("admin_setrole", admin_setrole_command))
    application.add_handler(CommandHandler("admin_vault", admin_vault_command))
    application.add_handler(CommandHandler("setnotif", setnotif_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("setvault", setvault_command))
    application.add_handler(CommandHandler("adduser", adduser_command))
    application.add_handler(CommandHandler("removeuser", removeuser_command))
    
    # File handler (handles all document/photo/video/audio/voice/animation)
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_file))
    
    # Callback handler (inline buttons)
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    # Person name reply handler (for admin approval flow)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_person_name))
    
    # Start background task workers
    await start_background_workers()
    
    print("Bot started")
    await application.run_polling()


async def shutdown():
    """Graceful shutdown."""
    print("Shutting down...")
    await stop_background_workers()
    await async_engine.dispose()
    print("Shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot stopped by user")
    except Exception as e:
        print(f"Fatal error: {e}")
        raise