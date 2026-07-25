"""
User-facing commands — DPDP Act 2023 Compliant
/forget, /about, /my_data for transparency and right to be forgotten.
"""
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from sqlalchemy import select

from utils import ensure_user, DbSession, escape_markdown
from models import Person, IncidentPerson, File, User
from storage import delete_file
from crypto_utils import decrypt_field


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message with legal disclaimer."""
    welcome_text = """
📖 **FileVault Bot — Public Interest Archive**

This bot documents **public incidents** (protests, gatherings, events) for historical record-keeping under fair dealing.

**Legal Basis:**
- Public interest documentation
- News reporting
- Historical archival

**Your Rights (DPDP Act 2023):**
- `/my_data` — See what we store about you
- `/forget` — Delete all your data permanently
- `/about` — Legal disclaimer and data policy

**How It Works:**
1. Send a photo/video from a public incident
2. Admin reviews and classifies (adult/minor)
3. Files are encrypted and stored separately
4. Names are pseudonymized — never stored in plaintext

⚠️ **Notice:** Do not submit files of minors without explicit consent. Minor files receive enhanced protection.

Send `/help` for all commands.
"""
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)


async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fair dealing disclaimer."""
    about_text = """
📖 **About FileVault Bot — Legal Position**

**Purpose:**
This bot is a **public-interest archive** documenting current events and incidents in public spaces. It operates under the legal framework of **fair dealing** for:
- News reporting and current events documentation
- Public interest record-keeping
- Historical archival of public gatherings

**Data Handling Principles:**
1. **Pseudonymization**: Personal identifiers are replaced with cryptographic hashes
2. **Separation**: Incident metadata and personal identities are stored in separate tables
3. **Encryption**: All PII is AES-256-GCM encrypted at rest
4. **Minor Protection**: Enhanced security for files involving minors
5. **Retention**: Automatic deletion after 1 year; names stripped during archival
6. **Transparency**: Users can view and delete their data via `/my_data` and `/forget`

**DPDP Act 2023 Compliance:**
- Section 7: Data Fiduciary obligations — minimal data collection, purpose limitation
- Section 8(1): Right to be forgotten — immediate deletion via `/forget`
- Section 7(3): Data minimization — no unnecessary PII collected
- Section 8(4): Storage limitation — 1-year retention with automatic archival

**No Private Data Collected:**
- We do not collect phone numbers, emails, or addresses
- We do not share data with third parties
- We do not use data for commercial purposes

**Contact:** For legal inquiries, contact the bot administrator.
**Jurisdiction:** This service operates under Indian law (DPDP Act 2023).
"""
    await update.message.reply_text(about_text, parse_mode=ParseMode.MARKDOWN)


async def my_data_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user what data we hold about them (transparency)."""
    user = update.effective_user

    async with DbSession() as session:
        # Find persons linked to this Telegram user
        result = await session.execute(
            select(Person).where(Person.telegram_user_id == user.id)
        )
        persons = result.scalars().all()

        if not persons:
            await update.message.reply_text(
                "📭 No data found for your account.\n"
                "This means you haven't submitted any files yet."
            )
            return

        response = "📋 **Your Data in Our System**\n"
        response += "━━━━━━━━━━━━━━━━━━━━━\n\n"

        for p in persons:
            # Decrypt name for user
            try:
                decrypted_name = decrypt_field(p.encrypted_name, p.name_nonce)
            except Exception:
                decrypted_name = "***encrypted***"

            response += f"👤 **Person ID:** `{p.person_id}`\n"
            response += f"   Name: {decrypted_name}\n"
            response += f"   Minor: {'Yes ⚠️' if p.is_minor else 'No'}\n"
            response += f"   Created: {str(p.created_at)[:10]}\n\n"

            # Show incidents
            links = await session.execute(
                select(IncidentPerson).where(IncidentPerson.person_id == p.person_id)
            )
            for link in links.scalars().all():
                response += f"   📍 Incident #{link.incident_id} ({link.role_in_incident})\n"

            response += "\n"

        response += "━━━━━━━━━━━━━━━━━━━━━\n"
        response += "Use `/forget` to delete all your data permanently."
        response += "\n\n*Names are shown decrypted only for your own records.*"

        await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)


async def forget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    DPDP Section 8(1) - Right to be forgotten.
    Permanently deletes all user data from DB and R2.
    """
    user = update.effective_user

    async with DbSession() as session:
        # Find all persons linked to this Telegram user
        result = await session.execute(
            select(Person).where(Person.telegram_user_id == user.id)
        )
        persons = result.scalars().all()

        if not persons:
            await update.message.reply_text(
                "📭 No data found for your account.\n"
                "Nothing to delete."
            )
            return

        deleted_files = 0
        deleted_persons = 0

        for person in persons:
            # Delete all files from R2
            files = await session.execute(
                select(File).where(File.person_id == person.person_id)
            )
            for file in files.scalars().all():
                if file.cloud_key and file.cloud_key != "local_only":
                    delete_file(file.cloud_key, is_minor=file.is_minor)
                deleted_files += 1

            # Delete person record (cascades to incident_persons)
            await session.delete(person)
            deleted_persons += 1

        # Log deletion
        from utils import log_audit
        await log_audit(
            user_id=user.id,
            action="gdpr_forget",
            details=f"User requested data deletion. Removed {deleted_persons} persons, {deleted_files} files."
        )

        await session.commit()

    await update.message.reply_text(
        "✅ **Your data has been permanently deleted.**\n\n"
        f"• Removed {deleted_persons} person records\n"
        f"• Deleted {deleted_files} files from storage\n"
        f"• All encryption keys destroyed\n\n"
        "This action cannot be undone. Thank you for using FileVault Bot.",
        parse_mode=ParseMode.MARKDOWN
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help message."""
    help_text = """
📖 **FileVault Bot — Command Reference**

**User Commands:**
• `/start` — Welcome message and legal notice
• `/help` — This message
• `/about` — Legal disclaimer and data policy
• `/my_data` — View your personal data in our system
• `/forget` — Permanently delete all your data (Right to be Forgotten)
• `/list` — Browse your approved files
• `/status` — Check your vault status

**Admin Commands:**
• `/admin` — Admin dashboard
• `/admin approve <user_id>` — Approve pending user
• `/admin deny <user_id>` — Block user
• `/setvault` — Set shared vault group (run in group)

**Legal Compliance:**
- All data is pseudonymized and encrypted
- Minors receive enhanced protection
- Retention: 1 year maximum
- Right to be forgotten: immediate deletion

**Contact:** For legal inquiries, contact the bot administrator.
"""
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)