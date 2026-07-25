"""
Test Suite for FileVault Evidence Vault System
DPDP Act 2023 Compliant Testing
"""
import os
import sys
import pytest
import asyncio
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Test configuration
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:password@localhost:5432/filevault_test"
)

# Skip tests if not configured
pytestmark = pytest.mark.asyncio