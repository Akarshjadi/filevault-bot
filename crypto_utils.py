"""
DPDP-compliant encryption utilities for pseudonymization.
Uses AES-256-GCM for field encryption and SHA-256 for hashing.
"""
import hashlib
import os
from dotenv import load_dotenv
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

load_dotenv()

MASTER_SALT = bytes.fromhex(os.getenv("ENCRYPTION_MASTER_SALT", ""))


def hash_person_name(name: str, salt: bytes = None) -> str:
    """SHA-256 hash for indexing. Same name + salt = same hash."""
    if salt is None:
        salt = MASTER_SALT
    return hashlib.sha256((name.lower().strip() + salt.hex()).encode()).hexdigest()


def encrypt_field(plaintext: str, associated_data: str = None) -> tuple[str, str]:
    """AES-256-GCM encrypt. Returns (ciphertext_hex, nonce_hex)."""
    aesgcm = AESGCM(MASTER_SALT[:32])
    nonce = os.urandom(12)
    ad = associated_data.encode() if associated_data else b""
    ct = aesgcm.encrypt(nonce, plaintext.encode(), ad)
    return ct.hex(), nonce.hex()


def decrypt_field(ciphertext_hex: str, nonce_hex: str, associated_data: str = None) -> str:
    """AES-256-GCM decrypt."""
    aesgcm = AESGCM(MASTER_SALT[:32])
    ct = bytes.fromhex(ciphertext_hex)
    nonce = bytes.fromhex(nonce_hex)
    ad = associated_data.encode() if associated_data else b""
    pt = aesgcm.decrypt(nonce, ct, ad)
    return pt.decode()


def generate_anonymous_token(file_hash: str, user_hash: str) -> str:
    """
    Generate a one-way anonymous token linking a submission to a user.
    This token CANNOT be reversed to identify the user.
    
    Purpose:
    - Allows uploader to check status of their submissions
    - Prevents spam by limiting submissions per user
    - Does NOT create a persistent identity trail
    
    Args:
        file_hash: SHA-256 hash of uploaded file
        user_hash: Hashed Telegram user ID
    
    Returns:
        str: 64-char hex token (HMAC-based)
    """
    import hmac
    # Use file hash + user hash + random salt to create token
    # The salt is stored in DB with the submission, but user_hash is not
    token_data = f"{file_hash}:{user_hash}".encode()
    return hmac.new(MASTER_SALT, token_data, hashlib.sha256).hexdigest()


def is_minor_by_name(name: str) -> bool:
    """Simple heuristic to flag potential minors (to be refined)."""
    age_keywords = ["minor", "child", "kid", "juvenile", "underage"]
    name_lower = name.lower()
    return any(kw in name_lower for kw in age_keywords)


def hash_telegram_user_id(telegram_id: int) -> str:
    """SHA-256 hash of Telegram user ID for pseudonymized storage."""
    return hashlib.sha256(f"tg:{telegram_id}:{MASTER_SALT.hex()}".encode()).hexdigest()


def encrypt_with_master_key(plaintext: str) -> tuple[bytes, bytes]:
    """AES-256-GCM encrypt using master salt as key. Returns (ciphertext, nonce)."""
    aesgcm = AESGCM(MASTER_SALT[:32])
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext.encode(), b"")
    return ct, nonce
