"""
Async database engine and session management for Supabase/PostgreSQL.
Uses SQLAlchemy 2.0 async pattern.
Optimized for Railway free tier (reduced connection pool).
"""
import os
from typing import AsyncGenerator, Optional

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine
)
from sqlalchemy import text

from models import Base

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:password@localhost:5432/filevault"
)

# Create async engine — reduced pool for Railway free tier
engine = create_async_engine(DATABASE_URL, echo=False, pool_size=2, max_overflow=3)

# Session factory
async_session_factory = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session for dependency injection."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """Create all tables if they don't exist.
    
    Run this once at startup.
    For production, run migrations instead.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Ensure indexes exist
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_files_vault_id ON files(vault_id);
            CREATE INDEX IF NOT EXISTS idx_files_sender ON files(sender_user_id);
            CREATE INDEX IF NOT EXISTS idx_files_unique_id ON files(telegram_file_unique_id);
        """))
        # GIN index for tag array search
        try:
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_files_tags ON files USING GIN (tags);
            """))
        except Exception:
            pass  # May fail if not PostgreSQL
        # Audit log indexes
        try:
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_audit_user_id ON audit_logs(user_id);
                CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action);
                CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_logs(created_at);
            """))
        except Exception:
            pass
    print("✅ Database tables and indexes created.")

    # Create storage directory if needed
    storage_base = os.getenv("STORAGE_BASE", "./vault_storage")
    if storage_base:
        os.makedirs(storage_base, exist_ok=True)


async def close_db():
    """Dispose of the engine on shutdown."""
    await engine.dispose()