"""
Shared utility functions for FileVault Bot.
"""
import os
from datetime import datetime, timezone
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session_factory
from models import User, UserRole, AuditLog


# Wrapper to use 'async with DbSession() as session'
class DbSession:
    async def __aenter__(self):
        self.session = async_session_factory()
        return self.session

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            await self.session.commit()
        else:
            await self.session.rollback()
        await self.session.close()


async def ensure_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Register user if not exists, check permissions (whitelist/blocked/pending)."""
    user = update.effective_user
    if not user:
        return False

    async with DbSession() as session:
        # Get or create user
        result = await session.execute(
            select(User).where(User.user_id == user.id)
        )
        db_user = result.scalar_one_or_none()

        if not db_user:
            # New user — add with PENDING role (requires admin approval)
            db_user = User(
                user_id=user.id,
                username=user.username or "",
                first_name=user.first_name or "",
                role=UserRole.PENDING,
                is_approved=False,
            )
            session.add(db_user)
            await session.flush()
            # Notify user they're pending
            if update.message:
                await update.message.reply_text(
                    "⏳ **Your account is pending admin approval.**\n\n"
                    "An admin will review your request shortly. "
                    "You'll be notified once approved.",
                    parse_mode="Markdown"
                )
            return False

        # Update last seen and username/first_name
        db_user.last_seen = datetime.now(timezone.utc)
        if user.username:
            db_user.username = user.username
        if user.first_name:
            db_user.first_name = user.first_name

        # Check if blocked
        if db_user.role == UserRole.BLOCKED:
            if update.message:
                await update.message.reply_text("⚠️ You are blocked from using this bot.")
            return False

        # Check if pending (not yet approved)
        if db_user.role == UserRole.PENDING and not db_user.is_approved:
            if update.message:
                await update.message.reply_text(
                    "⏳ **Your account is still pending admin approval.**\n\n"
                    "Please wait for an admin to approve your account.",
                    parse_mode="Markdown"
                )
            return False

        return True


async def is_admin(user_id: int) -> bool:
    """Check if a user has admin role."""
    async with DbSession() as session:
        result = await session.execute(
            select(User).where(User.user_id == user_id)
        )
        user = result.scalar_one_or_none()
        return user is not None and user.role == UserRole.ADMIN


async def log_audit(
    user_id: int,
    action: str,
    file_id: Optional[int] = None,
    details: Optional[str] = None,
    session: Optional[AsyncSession] = None,
):
    """Log an audit event. Uses provided session or creates a new one."""
    if session:
        log = AuditLog(
            user_id=user_id,
            action=action,
            file_id=file_id,
            details=details,
        )
        session.add(log)
    else:
        async with DbSession() as s:
            log = AuditLog(
                user_id=user_id,
                action=action,
                file_id=file_id,
                details=details,
            )
            s.add(log)


async def get_user_by_id(user_id: int) -> Optional[User]:
    """Fetch a user by their Telegram ID."""
    async with DbSession() as session:
        result = await session.execute(
            select(User).where(User.user_id == user_id)
        )
        return result.scalar_one_or_none()


async def get_all_users(page: int = 1, per_page: int = 20) -> tuple[list[User], int]:
    """Get paginated list of all users."""
    async with DbSession() as session:
        # Get total count
        result = await session.execute(
            text("SELECT COUNT(*) FROM users")
        )
        total = result.scalar()

        # Get paginated users
        offset = (page - 1) * per_page
        result = await session.execute(
            text("""
                SELECT * FROM users
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            {"limit": per_page, "offset": offset}
        )
        users = result.fetchall()
        return users, total


def map_telegram_file_type(file_type: str) -> str:
    """Map Telegram file types to our FileType enum values."""
    mapping = {
        "document": "document",
        "photo": "photo",
        "video": "video",
        "audio": "audio",
        "voice": "voice",
        "animation": "animation",
        "video_note": "video",
    }
    return mapping.get(file_type, "document")


def format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format."""
    if not size_bytes:
        return "0 B"
    size = float(size_bytes)
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def escape_markdown(text: str) -> str:
    """Escape special Markdown characters in user-provided text.
    
    Telegram's MarkdownV2 requires escaping: _ * [ ] ( ) ~ ` > # + - = | { } . !
    For regular Markdown (ParseMode.MARKDOWN), the main offenders are: _ * ` [
    This function handles both by escaping the most common problematic characters.
    """
    # Must escape backslash and backtick first to avoid double-escaping
    text = text.replace('\\', '\\\\')
    text = text.replace('`', '\\`')
    text = text.replace('*', '\\*')
    text = text.replace('_', '\\_')
    text = text.replace('[', '\\[')
    text = text.replace(']', '\\]')
    text = text.replace('(', '\\(')
    text = text.replace(')', '\\)')
    text = text.replace('~', '\\~')
    text = text.replace('>', '\\>')
    text = text.replace('#', '\\#')
    text = text.replace('+', '\\+')
    text = text.replace('-', '\\-')
    text = text.replace('=', '\\=')
    text = text.replace('|', '\\|')
    text = text.replace('{', '\\{')
    text = text.replace('}', '\\}')
    text = text.replace('.', '\\.')
    text = text.replace('!', '\\!')
    return text
