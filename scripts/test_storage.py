"""
Test script for R2 storage and database connectivity.
Run: python scripts/test_storage.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from storage import verify_connection, upload_file_bytes, generate_download_link, delete_file
from database import init_db, async_session_factory
from models import User, Vault, PendingFile, File, Base
from sqlalchemy import select, text

load_dotenv()


async def test_database():
    """Test database connection and basic operations."""
    print("\n=== Database Tests ===")
    try:
        await init_db()
        print("Tables created successfully")

        async with async_session_factory() as session:
            result = await session.execute(text("SELECT COUNT(*) FROM users"))
            user_count = result.scalar()
            print(f"Users in database: {user_count}")

            result = await session.execute(text("SELECT COUNT(*) FROM vaults"))
            vault_count = result.scalar()
            print(f"Vaults in database: {vault_count}")

            indexes = [
                "idx_files_vault_id", "idx_files_sender", "idx_files_unique_id",
                "idx_pending_files_sender", "idx_pending_files_status", "idx_files_tags"
            ]
            for idx in indexes:
                try:
                    result = await session.execute(text(f"SELECT indexname FROM pg_indexes WHERE indexname = :idx"), {"idx": idx})
                    if result.fetchone():
                        print(f"Index exists: {idx}")
                    else:
                        print(f"WARNING: Index missing: {idx}")
                except Exception:
                    pass

        print("Database tests passed!")
        return True
    except Exception as e:
        print(f"Database test failed: {e}")
        return False


async def test_storage():
    """Test R2 storage operations."""
    print("\n=== Storage Tests ===")
    if not verify_connection():
        print("R2 storage not configured or connection failed")
        return False

    test_data = b"This is a test file from FileVault Bot!"
    test_unique_id = "test_file_123"
    test_filename = "test_upload.txt"

    try:
        print("Testing upload...")
        cloud_key = upload_file_bytes(test_data, test_unique_id, test_filename)
        print(f"Uploaded to: {cloud_key}")

        print("Testing presigned URL generation...")
        url = generate_download_link(cloud_key, expires_in=3600)
        print(f"Presigned URL (valid 1h): {url[:80]}...")

        print("Testing delete...")
        success = delete_file(cloud_key)
        print(f"Deleted: {success}")

        print("Storage tests passed!")
        return True
    except Exception as e:
        print(f"Storage test failed: {e}")
        return False


async def test_models():
    """Test ORM models."""
    print("\n=== Model Tests ===")
    try:
        async with async_session_factory() as session:
            test_user = User(
                user_id=999999999,
                username="test_user",
                first_name="Test",
                role="PENDING",
                is_approved=False,
            )
            session.add(test_user)
            await session.flush()
            print(f"Created test user: {test_user.user_id}")

            result = await session.execute(select(User).where(User.user_id == 999999999))
            fetched = result.scalar_one_or_none()
            if fetched:
                print(f"Fetched user: {fetched.first_name}, role: {fetched.role}")
            else:
                print("ERROR: Could not fetch test user")
                return False

            await session.delete(fetched)
            await session.flush()
            print("Cleaned up test user")

        print("Model tests passed!")
        return True
    except Exception as e:
        print(f"Model test failed: {e}")
        return False


async def main():
    print("FileVault Bot - Storage & Database Tests")
    print("=" * 50)

    results = []
    results.append(await test_database())
    results.append(await test_storage())
    results.append(await test_models())

    print("\n" + "=" * 50)
    if all(results):
        print("All tests passed!")
        sys.exit(0)
    else:
        print("Some tests failed!")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())