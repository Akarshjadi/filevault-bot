"""
SQLAlchemy 2.0 Models — mirrors the PostgreSQL schema exactly.
Designed for Supabase/PostgreSQL with async support.
"""
from datetime import datetime
from typing import List, Optional
from enum import Enum as PyEnum

from sqlalchemy import (
    BigInteger, String, Text, ForeignKey, DateTime,
    ARRAY, Enum, func, UniqueConstraint, Boolean
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class UserRole(str, PyEnum):
    ADMIN = "ADMIN"
    WHITELISTED = "WHITELISTED"
    BLOCKED = "BLOCKED"
    PENDING = "PENDING"


class FileType(str, PyEnum):
    PHOTO = "photo"
    VIDEO = "video"
    DOCUMENT = "document"
    AUDIO = "audio"
    VOICE = "voice"
    ANIMATION = "animation"


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[Optional[str]] = mapped_column(String(32))
    first_name: Mapped[Optional[str]] = mapped_column(String(64))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.PENDING)
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    owned_vaults: Mapped[List["Vault"]] = relationship(back_populates="owner")
    uploaded_files: Mapped[List["File"]] = relationship(back_populates="sender")


class Vault(Base):
    __tablename__ = "vaults"

    vault_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_group_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False
    )
    owner_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(100), default="Personal Vault")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    owner: Mapped["User"] = relationship(back_populates="owned_vaults")
    files: Mapped[List["File"]] = relationship(
        back_populates="vault", cascade="all, delete-orphan"
    )


class File(Base):
    __tablename__ = "files"

    file_id_pk: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_file_id: Mapped[str] = mapped_column(Text, nullable=False)
    telegram_file_unique_id: Mapped[str] = mapped_column(
        Text, nullable=False, index=True
    )
    vault_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    vault_id: Mapped[int] = mapped_column(
        ForeignKey("vaults.vault_id", ondelete="CASCADE")
    )
    sender_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="RESTRICT")
    )

    file_type: Mapped[FileType] = mapped_column(Enum(FileType), nullable=False)
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger)
    file_name: Mapped[Optional[str]] = mapped_column(String(255))
    caption: Mapped[Optional[str]] = mapped_column(Text)
    tags: Mapped[List[str]] = mapped_column(ARRAY(String), default=list)

    topic_id: Mapped[Optional[int]] = mapped_column(nullable=True)
    saved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    vault: Mapped["Vault"] = relationship(back_populates="files")
    sender: Mapped["User"] = relationship(back_populates="uploaded_files")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    log_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    file_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("files.file_id_pk", ondelete="SET NULL"), nullable=True
    )
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )