"""
WebApp Handler - Opens Telegram Mini App for evidence recording
"""
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, CommandHandler
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)

# WebApp URL - set this to your hosted WebApp
WEBAPP_URL = "https://your-domain.com/webapp/index.html"


async def record_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /record - Opens the Evidence Recorder Mini App
    
    This launches the Telegram WebApp where users can:
    - Select photo/video
    - Detect faces in-browser (client-side)
    - Tag faces as bystander/official
    - Blur bystanders before upload
    - Submit only processed files
    """
    user = update.effective_user
    
    # Check if WebApp URL is configured
    if WEBAPP_URL == "https://your-domain.com/webapp/index.html":
        await update.message.reply_text(
            "⚠️ **WebApp Not Configured**\n\n"
            "The Evidence Recorder Mini App is not yet deployed.\n"
            "Please check back later or use the standard upload method.",
            parse_mode='Markdown'
        )
        return
    
    # Create WebApp button
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "📸 Open Evidence Recorder",
            web_app={"url": WEBAPP_URL}
        )]
    ])
    
    await update.message.reply_text(
        "🔒 **Privacy-First Evidence Recording**\n\n"
        "This Mini App runs entirely in your browser:\n"
        "• Face detection happens on your device\n"
        "• Bystander faces are blurred BEFORE upload\n"
        "• Server never sees unblurred footage\n"
        "• EXIF/GPS metadata is stripped automatically\n\n"
        "Tap the button below to start:",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )


async def webapp_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show information about the WebApp."""
    await update.message.reply_text(
        "📱 **Evidence Recorder Mini App**\n\n"
        "**How it works:**\n"
        "1. Tap the button to open the recorder\n"
        "2. Select a photo or video\n"
        "3. The app detects all faces automatically\n"
        "4. Tag each person:\n"
        "   • 👤 Bystander → gets blurred (default)\n"
        "   • 👮 On-duty Official → stays unblurred\n"
        "5. Add incident details\n"
        "6. Submit - only the blurred version is uploaded\n\n"
        "**Privacy Guarantees:**\n"
        "✓ Face detection runs in your browser only\n"
        "✓ Original footage never leaves your device\n"
        "✓ Server only receives processed files\n"
        "✓ No EXIF/GPS metadata\n"
        "✓ No identity linkage beyond rate-limiting\n\n"
        "Use /record to start",
        parse_mode='Markdown'
    )


def get_webapp_handlers():
    """Return list of WebApp handlers."""
    return [
        CommandHandler("record", record_command),
        CommandHandler("webapp_info", webapp_info_command),
    ]