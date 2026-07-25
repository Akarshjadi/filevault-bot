"""
User Registration Handler
Manages Telegram user registration with consent and age verification.
"""
import logging
import hashlib
from typing import Optional
from datetime import datetime

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, CallbackQueryHandler, MessageHandler, filters

from models_vault import Profile, get_session
from crypto_utils import hash_telegram_user_id, encrypt_with_master_key
from database import async_session_factory

logger = logging.getLogger(__name__)

# Conversation states
REGISTER_AGE, REGISTER_SELFIE = range(2)

# Consent text versions
CONSENT_TEXT_VERSION = "1.0"
CONSENT_TEXT = """
📋 **Privacy & Consent Agreement** (Version {version})

By using the FileVault Evidence System, you agree to:

1. **Face Blurring**: All faces in submitted media will be blurred by default for privacy protection.

2. **Explicit Consent**: Your face will only be unblurred if you provide explicit consent via a verified action.

3. **Minor Protection**: Minors (under 18) will NEVER have their faces unblurred, regardless of consent.

4. **Data Storage**: Original files are stored securely in encrypted cloud storage and are never publicly accessible.

5. **Right to be Forgotten**: You can request deletion of your data at any time using /forget.

6. **Retention**: Personal data is retained for 90 days unless you request earlier deletion.

7. **DPDP Act Compliance**: This system complies with India's Digital Personal Data Protection Act, 2023.

Do you accept this agreement?
"""


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Handle /start command - initiate registration.
    
    Flow:
    1. Check if user already registered
    2. Present consent text
    3. Ask for age verification
    """
    user = update.effective_user
    telegram_id = user.id
    
    # Hash Telegram ID for storage
    telegram_id_hash = hash_telegram_user_id(telegram_id)
    
    async with async_session_factory() as session:
        # Check if already registered
        from sqlalchemy import select
        result = await session.execute(
            select(Profile).where(Profile.telegram_user_id_hash == telegram_id_hash)
        )
        existing_profile = result.scalar_one_or_none()
        
        if existing_profile:
            logger.info(f"User {telegram_id_hash} already registered")
            await update.message.reply_text(
                "✅ You are already registered!\n\n"
                "Use /submit to upload evidence.\n"
                "Use /my_data to view your profile.\n"
                "Use /forget to delete your data.",
                parse_mode='Markdown'
            )
            return ConversationHandler.END
    
    # New user - present consent
    context.user_data['telegram_id'] = telegram_id
    context.user_data['telegram_id_hash'] = telegram_id_hash
    
    keyboard = [
        [InlineKeyboardButton("✅ I Accept", callback_data="consent_accept"),
         InlineKeyboardButton("❌ I Decline", callback_data="consent_decline")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        CONSENT_TEXT.format(version=CONSENT_TEXT_VERSION),
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    
    return REGISTER_AGE


async def consent_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle consent acceptance/decline."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "consent_decline":
        await query.edit_message_text(
            "❌ Registration cancelled.\n"
            "You must accept the consent agreement to use this bot.\n"
            "Use /start to try again."
        )
        return ConversationHandler.END
    
    # Accepted - ask for age verification
    keyboard = [
        [InlineKeyboardButton("✅ Yes, I am 18 or older", callback_data="age_yes"),
         InlineKeyboardButton("❌ No, I am under 18", callback_data="age_no")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "📅 **Age Verification**\n\n"
        "Please confirm: Are you 18 years of age or older?\n\n"
        "⚠️ *Note: This is a legal requirement for consent under DPDP Act. "
        "No verification beyond your declaration, but this is logged.*\n\n"
        "If you are under 18, you can still use the bot to submit evidence, "
        "but your face will NEVER be unblurred in any published materials.",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    
    return REGISTER_AGE


async def age_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle age verification response."""
    query = update.callback_query
    await query.answer()
    
    telegram_id = context.user_data['telegram_id']
    telegram_id_hash = context.user_data['telegram_id_hash']
    is_18_plus = query.data == "age_yes"
    
    try:
        async with async_session_factory() as session:
            # Encrypt Telegram ID
            encrypted_id, id_nonce = encrypt_with_master_key(str(telegram_id))
            
            # Create profile
            profile = Profile(
                telegram_user_id_hash=telegram_id_hash,
                encrypted_telegram_id=encrypted_id.decode() if isinstance(encrypted_id, bytes) else encrypted_id,
                telegram_id_nonce=id_nonce.decode() if isinstance(id_nonce, bytes) else id_nonce,
                age_verified=is_18_plus,
                age_verified_at=datetime.utcnow() if is_18_plus else None,
                accepted_consent_version=CONSENT_TEXT_VERSION,
                consent_accepted_at=datetime.utcnow(),
            )
            
            session.add(profile)
            await session.commit()
            await session.refresh(profile)
            
            logger.info(f"Registered new user: {telegram_id_hash}, age_verified={is_18_plus}")
            
            # Success message
            if is_18_plus:
                age_status = "✅ Age verified (18+)"
            else:
                age_status = "⚠️ Minor user - faces will never be unblurred"
            
            await query.edit_message_text(
                f"✅ **Registration Complete!**\n\n"
                f"{age_status}\n"
                f"Consent version: {CONSENT_TEXT_VERSION}\n\n"
                f"**Next Steps:**\n"
                f"/submit - Upload evidence\n"
                f"/my_data - View your profile\n"
                f"/forget - Delete your data\n"
                f"/help - Get help\n\n"
                f"*Remember: All faces are blurred by default.*",
                parse_mode='Markdown'
            )
            
            return ConversationHandler.END
    
    except Exception as e:
        logger.error(f"Registration failed for {telegram_id_hash}: {e}")
        await query.edit_message_text(
            "❌ Registration failed. Please try again later.\n"
            "Use /start to retry."
        )
        return ConversationHandler.END


async def cancel_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel registration process."""
    await update.message.reply_text(
        "❌ Registration cancelled.\nUse /start to try again."
    )
    return ConversationHandler.END


def get_registration_handler() -> ConversationHandler:
    """Return the registration conversation handler."""
    return ConversationHandler(
        entry_points=[CommandHandler("start", start_command)],
        states={
            REGISTER_AGE: [
                CallbackQueryHandler(consent_callback, pattern="^consent_"),
                CallbackQueryHandler(age_callback, pattern="^age_")
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_registration)],
        per_user=True,
        per_chat=True,
    )


async def resend_consent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Resend consent text for re-acceptance (e.g., after version update)."""
    telegram_id = update.effective_user.id
    telegram_id_hash = hash_telegram_user_id(telegram_id)
    
    async with async_session_factory() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(Profile).where(Profile.telegram_user_id_hash == telegram_id_hash)
        )
        profile = result.scalar_one_or_none()
        
        if not profile:
            await update.message.reply_text("❌ You must register first. Use /start")
            return ConversationHandler.END
        
        # Present new consent
        keyboard = [
            [InlineKeyboardButton("✅ I Accept New Terms", callback_data="consent_update_accept")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"📋 **Updated Consent Agreement** (Version {CONSENT_TEXT_VERSION})\n\n"
            f"Our terms have been updated. Please review and accept:\n\n"
            f"{CONSENT_TEXT.format(version=CONSENT_TEXT_VERSION)}\n\n"
            f"*You must accept to continue using the bot.*",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
        return REGISTER_AGE


async def consent_update_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle consent update acceptance."""
    query = update.callback_query
    await query.answer()
    
    telegram_id = update.effective_user.id
    telegram_id_hash = hash_telegram_user_id(telegram_id)
    
    async with async_session_factory() as session:
        from sqlalchemy import select, update as sa_update
        await session.execute(
            sa_update(Profile)
            .where(Profile.telegram_user_id_hash == telegram_id_hash)
            .values(
                accepted_consent_version=CONSENT_TEXT_VERSION,
                consent_accepted_at=datetime.utcnow()
            )
        )
        await session.commit()
    
    await query.edit_message_text(
        "✅ **Consent Updated!**\n\n"
        "Thank you for accepting the updated terms.\n"
        "Use /submit to continue."
    )
    
    return ConversationHandler.END


def get_consent_update_handler() -> ConversationHandler:
    """Return handler for consent updates."""
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(consent_callback, pattern="^consent_accept$")],
        states={
            REGISTER_AGE: [
                CallbackQueryHandler(consent_update_callback, pattern="^consent_update_accept$")
            ],
        },
        fallbacks=[],
        per_user=True,
        per_chat=True,
    )