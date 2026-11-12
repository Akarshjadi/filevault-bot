"""
Tier 3 — Admin & Settings Commands
Full admin panel with approval flow, audit logging, and user management.
"""
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from sqlalchemy import text, select

from utils import (
    ensure_user, DbSession, is_admin, log_audit,
    get_user_by_id, get_all_users, format_file_size, escape_markdown,
)
from models import User, Vault, UserRole


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main admin dashboard — shows system overview and quick actions."""
    if not await ensure_user(update, context):
        return

    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("⚠️ Only admins can use this command.")
        return

    async with DbSession() as session:
        # Get counts
        result = await session.execute(text("SELECT COUNT(*) FROM users"))
        total_users = result.scalar()

        result = await session.execute(
            text("SELECT COUNT(*) FROM users WHERE role = 'pending' AND is_approved = false")
        )
        pending_users = result.scalar()

        result = await session.execute(text("SELECT COUNT(*) FROM files"))
        total_files = result.scalar()

        result = await session.execute(
            text("SELECT COALESCE(SUM(file_size), 0) FROM files")
        )
        total_size = result.scalar()

        result = await session.execute(text("SELECT COUNT(*) FROM vaults"))
        total_vaults = result.scalar()

    text_msg = (
        "🛡️ **Admin Dashboard**\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 **Users:** {total_users} (⏳ {pending_users} pending)\n"
        f"📁 **Files:** {total_files}\n"
        f"💾 **Storage:** {format_file_size(total_size)}\n"
        f"🏛️ **Vaults:** {total_vaults}\n\n"
        "**Quick Actions:**\n"
        "`/admin users` — List all users\n"
        "`/admin approve <id>` — Approve pending user\n"
        "`/admin deny <id>` — Deny pending user\n"
        "`/admin stats` — Detailed statistics\n"
        "`/admin logs` — View audit logs\n"
        "`/admin broadcast <msg>` — Message all users\n"
        "`/admin setrole <id> <role>` — Change user role\n"
        "`/admin vault <id>` — View user's vault\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 Use `/help` for all commands."
    )

    await update.message.reply_text(text_msg, parse_mode=ParseMode.MARKDOWN)


async def admin_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all users with pagination."""
    if not await ensure_user(update, context):
        return

    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("⚠️ Only admins can use this command.")
        return

    page = 1
    if context.args and len(context.args) > 0:
        try:
            page = max(1, int(context.args[0]))
        except ValueError:
            page = 1

    users, total = await get_all_users(page=page, per_page=15)

    if not users:
        await update.message.reply_text("📭 No users found.")
        return

    total_pages = max(1, (total + 14) // 15)
    response = f"👥 **All Users** (Page {page}/{total_pages})\n━━━━━━━━━━━━━━━━━━━━━\n"

    for u in users:
        user_id, username, first_name, role, is_approved, approved_by, approved_at, last_seen, notif_enabled, created_at = u
        status_icon = "✅" if is_approved else "⏳" if role == "pending" else "❌" if role == "blocked" else "🛡️"
        last_seen_str = str(last_seen)[:10] if last_seen else "Never"
        safe_first_name = escape_markdown(first_name or "Unknown")
        safe_username = escape_markdown(username) if username else ""
        response += (
            f"{status_icon} `{user_id}` — **{safe_first_name}**"
            f"{' (@' + safe_username + ')' if username else ''}\n"
            f"   🔑 `{role.upper()}` | 🕐 Last: {last_seen_str}\n\n"
        )

    response += f"Total: {total} users"
    await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)


async def admin_approve_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Approve a pending user and notify them."""
    if not await ensure_user(update, context):
        return

    admin_id = update.effective_user.id
    if not await is_admin(admin_id):
        await update.message.reply_text("⚠️ Only admins can use this command.")
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ Usage: `/admin approve <user_id>`\nExample: `/admin approve 123456789`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ Invalid user ID. Must be a number.")
        return

    async with DbSession() as session:
        result = await session.execute(
            select(User).where(User.user_id == target_id)
        )
        user = result.scalar_one_or_none()

        if not user:
            await update.message.reply_text(f"⚠️ User `{target_id}` not found.")
            return

        if user.is_approved:
            await update.message.reply_text(f"⚠️ User `{target_id}` is already approved.")
            return

        # Approve the user
        user.is_approved = True
        user.role = UserRole.WHITELISTED
        user.approved_by = admin_id
        user.approved_at = datetime.now(timezone.utc)

        # Log audit
        await log_audit(
            user_id=admin_id,
            action="approve_user",
            details=f"Approved user {target_id}",
            session=session,
        )

    # Notify the approved user
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=(
                "✅ **Your account has been approved!**\n\n"
                "You now have full access to FileVault Bot.\n"
                "• Send any file to save it to your vault\n"
                "• Use `/help` to see all commands\n"
                "• Use `/list` to browse your files\n\n"
                "Welcome aboard! 🎉"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass  # User may have blocked the bot

    await update.message.reply_text(
        f"✅ User `{target_id}` has been approved and notified.",
        parse_mode=ParseMode.MARKDOWN
    )


async def admin_deny_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deny a pending user and notify them."""
    if not await ensure_user(update, context):
        return

    admin_id = update.effective_user.id
    if not await is_admin(admin_id):
        await update.message.reply_text("⚠️ Only admins can use this command.")
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ Usage: `/admin deny <user_id>`\nExample: `/admin deny 123456789`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ Invalid user ID. Must be a number.")
        return

    async with DbSession() as session:
        result = await session.execute(
            select(User).where(User.user_id == target_id)
        )
        user = result.scalar_one_or_none()

        if not user:
            await update.message.reply_text(f"⚠️ User `{target_id}` not found.")
            return

        if user.role == UserRole.BLOCKED:
            await update.message.reply_text(f"⚠️ User `{target_id}` is already blocked.")
            return

        # Block the user
        user.role = UserRole.BLOCKED
        user.is_approved = False

        # Log audit
        await log_audit(
            user_id=admin_id,
            action="deny_user",
            details=f"Denied/blocked user {target_id}",
            session=session,
        )

    # Notify the denied user
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=(
                "❌ **Your account request has been denied.**\n\n"
                "You do not have access to FileVault Bot at this time. "
                "Please contact an administrator if you believe this is an error."
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass

    await update.message.reply_text(
        f"✅ User `{target_id}` has been denied/blocked and notified.",
        parse_mode=ParseMode.MARKDOWN
    )


async def admin_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show detailed system statistics."""
    if not await ensure_user(update, context):
        return

    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("⚠️ Only admins can use this command.")
        return

    async with DbSession() as session:
        # User stats
        result = await session.execute(text("""
            SELECT 
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE role = 'admin') as admins,
                COUNT(*) FILTER (WHERE role = 'whitelisted' AND is_approved) as approved,
                COUNT(*) FILTER (WHERE role = 'pending' AND NOT is_approved) as pending,
                COUNT(*) FILTER (WHERE role = 'blocked') as blocked
            FROM users
        """))
        user_stats = result.fetchone()

        # File stats by type
        result = await session.execute(text("""
            SELECT file_type, COUNT(*), COALESCE(SUM(file_size), 0)
            FROM files GROUP BY file_type ORDER BY COUNT(*) DESC
        """))
        file_by_type = result.fetchall()

        # Vault stats
        result = await session.execute(text("""
            SELECT COUNT(*), COALESCE(AVG(file_count), 0)
            FROM (
                SELECT v.vault_id, COUNT(f.file_id_pk) as file_count
                FROM vaults v LEFT JOIN files f ON v.vault_id = f.vault_id
                GROUP BY v.vault_id
            ) sub
        """))
        vault_count, avg_files = result.fetchone()

        # Recent activity
        result = await session.execute(text("""
            SELECT action, COUNT(*) as cnt
            FROM audit_logs
            WHERE created_at > NOW() - INTERVAL '24 hours'
            GROUP BY action ORDER BY cnt DESC
        """))
        recent_activity = result.fetchall()

    text_msg = (
        "📊 **System Statistics**\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "**👥 Users**\n"
        f"• Total: {user_stats[0]}\n"
        f"• Admins: {user_stats[1]}\n"
        f"• Approved: {user_stats[2]}\n"
        f"• Pending: {user_stats[3]}\n"
        f"• Blocked: {user_stats[4]}\n\n"
        "**🏛️ Vaults**\n"
        f"• Total: {vault_count}\n"
        f"• Avg files/vault: {avg_files:.1f}\n\n"
        "**📁 Files by Type**\n"
    )

    for ft, count, size in file_by_type:
        text_msg += f"• {ft}: {count} ({format_file_size(size)})\n"

    text_msg += "\n**🕐 Last 24h Activity**\n"
    if recent_activity:
        for action, cnt in recent_activity:
            text_msg += f"• {action}: {cnt}\n"
    else:
        text_msg += "• No activity in last 24h\n"

    await update.message.reply_text(text_msg, parse_mode=ParseMode.MARKDOWN)


async def admin_logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View audit logs with pagination."""
    if not await ensure_user(update, context):
        return

    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("⚠️ Only admins can use this command.")
        return

    page = 1
    if context.args and len(context.args) > 0:
        try:
            page = max(1, int(context.args[0]))
        except ValueError:
            page = 1

    per_page = 10
    offset = (page - 1) * per_page

    async with DbSession() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM audit_logs"))
        total = result.scalar()

        result = await session.execute(
            text("""
                SELECT al.log_id, al.user_id, al.action, al.file_id, al.details, al.created_at
                FROM audit_logs al
                ORDER BY al.created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            {"limit": per_page, "offset": offset}
        )
        logs = result.fetchall()

    if not logs:
        await update.message.reply_text("📭 No audit logs found.")
        return

    total_pages = max(1, (total + per_page - 1) // per_page)
    response = f"📋 **Audit Logs** (Page {page}/{total_pages})\n━━━━━━━━━━━━━━━━━━━━━\n"

    for log_id, user_id, action, file_id, details, created_at in logs:
        file_str = f" | File: {file_id}" if file_id else ""
        detail_str = f" | {details}" if details else ""
        response += (
            f"`#{log_id}` **{action}** by `{user_id}`{file_str}\n"
            f"   🕐 {str(created_at)[:19]}{detail_str}\n\n"
        )

    await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)


async def admin_broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message to all approved users."""
    if not await ensure_user(update, context):
        return

    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("⚠️ Only admins can use this command.")
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ Usage: `/admin broadcast <message>`\n"
            "Example: `/admin broadcast Bot will be down for maintenance at 10pm.`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    message = ' '.join(context.args)
    admin_id = update.effective_user.id

    async with DbSession() as session:
        result = await session.execute(
            text("""
                SELECT user_id FROM users 
                WHERE is_approved = true AND role != 'blocked'
            """)
        )
        user_ids = [row[0] for row in result.fetchall()]

        # Log audit
        await log_audit(
            user_id=admin_id,
            action="broadcast",
            details=f"Broadcast to {len(user_ids)} users: {message[:100]}",
            session=session,
        )

    # Send to all users
    sent = 0
    failed = 0
    for uid in user_ids:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=(
                    f"📢 **Admin Broadcast**\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"{message}\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"_This is an official bot announcement._"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
            sent += 1
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"📢 Broadcast sent!\n"
        f"✅ Delivered: {sent}\n"
        f"❌ Failed: {failed}\n"
        f"👥 Total recipients: {len(user_ids)}",
        parse_mode=ParseMode.MARKDOWN
    )


async def admin_setrole_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set a user's role (admin/whitelisted/blocked)."""
    if not await ensure_user(update, context):
        return

    admin_id = update.effective_user.id
    if not await is_admin(admin_id):
        await update.message.reply_text("⚠️ Only admins can use this command.")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "⚠️ Usage: `/admin setrole <user_id> <role>`\n"
            "Roles: `admin`, `whitelisted`, `blocked`\n"
            "Example: `/admin setrole 123456789 admin`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        target_id = int(context.args[0])
        new_role = context.args[1].lower()
    except ValueError:
        await update.message.reply_text("⚠️ Invalid user ID. Must be a number.")
        return

    if new_role not in ['admin', 'whitelisted', 'blocked']:
        await update.message.reply_text(
            "⚠️ Invalid role. Must be: `admin`, `whitelisted`, or `blocked`.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    async with DbSession() as session:
        result = await session.execute(
            select(User).where(User.user_id == target_id)
        )
        user = result.scalar_one_or_none()

        if not user:
            await update.message.reply_text(f"⚠️ User `{target_id}` not found.")
            return

        # Update role
        user.role = UserRole(new_role)
        if new_role == 'whitelisted':
            user.is_approved = True
        elif new_role == 'blocked':
            user.is_approved = False

        # Log audit
        await log_audit(
            user_id=admin_id,
            action="set_role",
            details=f"Set user {target_id} role to {new_role}",
            session=session,
        )

    await update.message.reply_text(
        f"✅ User `{target_id}` role set to **{new_role.upper()}**.",
        parse_mode=ParseMode.MARKDOWN
    )


async def admin_vault_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View the shared vault information."""
    if not await ensure_user(update, context):
        return

    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("⚠️ Only admins can use this command.")
        return

    async with DbSession() as session:
        result = await session.execute(select(Vault))
        vault = result.scalar_one_or_none()

        if not vault:
            await update.message.reply_text(
                "⚠️ No vault configured yet. Use `/setvault` in a group.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        result = await session.execute(
            text("""
                SELECT COUNT(*), COALESCE(SUM(file_size), 0)
                FROM files WHERE vault_id = :vid
            """),
            {"vid": vault.vault_id}
        )
        file_count, total_size = result.fetchone()

        # Get recent files
        result = await session.execute(
            text("""
                SELECT file_id_pk, file_name, file_type, saved_at, sender_user_id
                FROM files WHERE vault_id = :vid
                ORDER BY saved_at DESC LIMIT 10
            """),
            {"vid": vault.vault_id}
        )
        recent_files = result.fetchall()

    safe_vault_name = escape_markdown(vault.name)
    text_msg = (
        f"🏛️ **Shared Vault Info**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📛 **Name:** {safe_vault_name}\n"
        f"🆔 **Vault ID:** `{vault.vault_id}`\n"
        f"🏠 **Group ID:** `{vault.telegram_group_id}`\n"
        f"📁 **Files:** {file_count}\n"
        f"💾 **Storage:** {format_file_size(total_size)}\n"
        f"📅 **Created:** {str(vault.created_at)[:10]}\n\n"
    )

    if recent_files:
        text_msg += "**Recent Files:**\n"
        for fid, fname, ftype, saved_at, sender_id in recent_files:
            text_msg += f"• `{fid}` {fname or 'Unknown'} ({ftype}) — by `{sender_id}` — {str(saved_at)[:10]}\n"

    await update.message.reply_text(text_msg, parse_mode=ParseMode.MARKDOWN)


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current user settings."""
    if not await ensure_user(update, context):
        return

    user = update.effective_user

    async with DbSession() as session:
        result = await session.execute(
            select(User).where(User.user_id == user.id)
        )
        db_user = result.scalar_one_or_none()

    notif_status = "Enabled ✅" if db_user and db_user.notifications_enabled else "Disabled ❌"

    safe_username = escape_markdown(user.username or "")
    safe_first_name = escape_markdown(user.first_name or "")
    settings_text = (
        f"⚙️ **Your Settings**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 **User:** @{safe_username or safe_first_name}\n"
        f"🆔 **ID:** `{user.id}`\n"
        f"🔑 **Role:** `{db_user.role.value.upper() if db_user else 'PENDING'}`\n"
        f"🔔 **Notifications:** {notif_status}\n"
        f"\n"
        f"**Available Commands:**\n"
        f"• `/setnotif on|off` — Toggle notifications\n"
        f"• `/whoami` — View your info\n"
        f"• `/status` — View vault status\n"
    )

    await update.message.reply_text(settings_text, parse_mode=ParseMode.MARKDOWN)


async def setnotif_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle notifications — persisted in database."""
    if not await ensure_user(update, context):
        return

    if not context.args or context.args[0].lower() not in ['on', 'off']:
        await update.message.reply_text(
            "⚠️ Usage: `/setnotif <on/off>`\nExample: `/setnotif off`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    enabled = context.args[0].lower() == 'on'
    user_id = update.effective_user.id

    async with DbSession() as session:
        await session.execute(
            text("UPDATE users SET notifications_enabled = :enabled WHERE user_id = :uid"),
            {"enabled": enabled, "uid": user_id}
        )

    status = "enabled ✅" if enabled else "disabled ❌"
    await update.message.reply_text(f"🔔 Notifications {status}")


async def setvault_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the shared vault group (admin-only). Run this inside the target group."""
    if not await ensure_user(update, context):
        return

    user_id = update.effective_user.id
    if not await is_admin(user_id):
        await update.message.reply_text("⚠️ Only admins can use this command.")
        return

    chat = update.effective_chat

    # Only works in groups
    if chat.type not in ['group', 'supergroup']:
        await update.message.reply_text(
            "⚠️ This command must be run in a group (or supergroup)."
        )
        return

    async with DbSession() as session:
        # Get or create the single shared vault
        result = await session.execute(select(Vault))
        vault = result.scalar_one_or_none()
        
        if vault:
            # Update existing vault
            vault.telegram_group_id = chat.id
            vault.name = chat.title or "Shared Vault"
        else:
            # Create shared vault
            vault = Vault(
                telegram_group_id=chat.id,
                name=chat.title or "Shared Vault",
            )
            session.add(vault)

        # Log audit
        await log_audit(
            user_id=user_id,
            action="set_vault",
            details=f"Set shared vault to group {chat.id} ({chat.title})",
            session=session,
        )

    safe_chat_title = escape_markdown(chat.title or "Unknown")
    await update.message.reply_text(
        f"✅ Shared vault set to this group!\n"
        f"📛 Group: **{safe_chat_title}**\n"
        f"🆔 Chat ID: `{chat.id}`\n"
        f"💡 All approved files will be sent here.",
        parse_mode=ParseMode.MARKDOWN
    )


async def adduser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add user to whitelist (admin-only)."""
    if not await ensure_user(update, context):
        return

    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("⚠️ Only admins can use this command.")
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ Usage: `/adduser <user_id>`\nExample: `/adduser 123456789`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        new_user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ Invalid user ID. Must be a number.")
        return

    async with DbSession() as session:
        result = await session.execute(
            select(User).where(User.user_id == new_user_id)
        )
        existing_user = result.scalar_one_or_none()

        if existing_user:
            if existing_user.role == UserRole.WHITELISTED and existing_user.is_approved:
                await update.message.reply_text(f"⚠️ User `{new_user_id}` is already whitelisted.")
                return
            # Update role to whitelisted
            existing_user.role = UserRole.WHITELISTED
            existing_user.is_approved = True
            existing_user.approved_by = update.effective_user.id
            existing_user.approved_at = datetime.now(timezone.utc)
        else:
            # Create new user entry
            new_user = User(
                user_id=new_user_id,
                username="",
                first_name=f"User {new_user_id}",
                role=UserRole.WHITELISTED,
                is_approved=True,
                approved_by=update.effective_user.id,
                approved_at=datetime.now(timezone.utc),
            )
            session.add(new_user)

        # Log audit
        await log_audit(
            user_id=update.effective_user.id,
            action="add_user",
            details=f"Added/approved user {new_user_id}",
            session=session,
        )

    # Notify the user
    try:
        await context.bot.send_message(
            chat_id=new_user_id,
            text=(
                "✅ **Your account has been approved!**\n\n"
                "You now have full access to FileVault Bot.\n"
                "• Send any file to save it to your vault\n"
                "• Use `/help` to see all commands\n\n"
                "Welcome aboard! 🎉"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass

    await update.message.reply_text(
        f"✅ User `{new_user_id}` has been added to whitelist and notified.",
        parse_mode=ParseMode.MARKDOWN
    )


async def removeuser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove user from whitelist (admin-only)."""
    if not await ensure_user(update, context):
        return

    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("⚠️ Only admins can use this command.")
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ Usage: `/removeuser <user_id>`\nExample: `/removeuser 123456789`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        remove_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ Invalid user ID. Must be a number.")
        return

    async with DbSession() as session:
        result = await session.execute(
            select(User).where(User.user_id == remove_id)
        )
        user = result.scalar_one_or_none()

        if not user:
            await update.message.reply_text(f"⚠️ User `{remove_id}` not found.")
            return

        if user.role == UserRole.ADMIN:
            await update.message.reply_text("⚠️ Cannot block an admin user.")
            return

        # Block the user
        user.role = UserRole.BLOCKED
        user.is_approved = False

        # Log audit
        await log_audit(
            user_id=update.effective_user.id,
            action="remove_user",
            details=f"Blocked user {remove_id}",
            session=session,
        )

    # Notify the user
    try:
        await context.bot.send_message(
            chat_id=remove_id,
            text=(
                "❌ **Your access has been revoked.**\n\n"
                "You can no longer use FileVault Bot. "
                "Please contact an administrator if you believe this is an error."
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass

    await update.message.reply_text(
        f"✅ User `{remove_id}` has been blocked from using the bot.",
        parse_mode=ParseMode.MARKDOWN
    )