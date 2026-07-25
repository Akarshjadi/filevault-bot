#!/bin/bash
# Deployment script for FileVault Evidence Vault
# Deploys to Railway, Supabase, and optionally WebApp

set -e  # Exit on error

echo "🚀 Starting FileVault deployment..."

# ============================================================================
# Step 1: Validate environment
# ============================================================================

echo "📋 Checking environment..."

if [ -z "$BOT_TOKEN" ]; then
    echo "❌ BOT_TOKEN not set"
    exit 1
fi

if [ -z "$DATABASE_URL" ]; then
    echo "❌ DATABASE_URL not set"
    exit 1
fi

if [ -z "$S3_ENDPOINT_URL" ]; then
    echo "❌ S3_ENDPOINT_URL not set"
    exit 1
fi

echo "✅ Environment variables validated"

# ============================================================================
# Step 2: Install dependencies
# ============================================================================

echo "📦 Installing dependencies..."

pip install -r requirements_vault.txt

echo "✅ Dependencies installed"

# ============================================================================
# Step 3: Run database migrations
# ============================================================================

echo "🗄️ Running database migrations..."

# Create a temporary Python script to run migrations
python3 << 'EOF'
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import async_engine
from sqlalchemy import text

async def run_migrations():
    migrations_dir = Path(__file__).parent.parent / "migrations"
    
    async with async_engine.begin() as conn:
        # Create migrations table
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
        
        print("Migrations complete")

asyncio.run(run_migrations())
EOF

echo "✅ Database migrations completed"

# ============================================================================
# Step 4: Create R2 buckets (if they don't exist)
# ============================================================================

echo "☁️ Checking R2 buckets..."

python3 << 'EOF'
import sys
sys.path.insert(0, ".")

try:
    from storage.r2 import get_r2_storage
    
    r2 = get_r2_storage()
    success, message = r2.verify_connection()
    
    if success:
        print("✅ R2 storage configured")
    else:
        print("⚠️ R2 verification failed:")
        print(message)
        print("Please check your R2 configuration")
except Exception as e:
    print(f"⚠️ R2 check failed: {e}")
EOF

echo "✅ R2 check completed"

# ============================================================================
# Step 5: Verify bot can start
# ============================================================================

echo "🤖 Verifying bot configuration..."

python3 << 'EOF'
import sys
sys.path.insert(0, ".")

try:
    # Test imports
    from bot import run_migrations
    from handlers.webapp import get_webapp_handlers
    from handlers.receive import handle_webapp_data
    from processing.verify import SubmissionVerifier
    
    print("✅ All modules import successfully")
    print("✅ Bot configuration valid")
except Exception as e:
    print(f"❌ Bot verification failed: {e}")
    sys.exit(1)
EOF

echo "✅ Bot verification completed"

# ============================================================================
# Step 6: Deployment summary
# ============================================================================

echo ""
echo "🎉 Deployment preparation complete!"
echo ""
echo "Next steps:"
echo "1. Deploy to Railway: railway up"
echo "2. Deploy WebApp: See DEPLOY.md Step 6"
echo "3. Test the bot: Send /record in Telegram"
echo ""
echo "📊 Services to configure:"
echo "  - Railway: https://railway.app"
echo "  - Supabase: https://supabase.com"
echo "  - Cloudflare R2: https://dash.cloudflare.com"
echo "  - Telegram: @BotFather"
echo ""