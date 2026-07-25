"""
SQLAlchemy 2.0 Models — DPDP Act 2023 Compliant
Two-key pseudonymization, separate adult/minor storage, retention policies.
"""
from datetime import datetime
from typing import Optional, List
from enum import Enum as PyEnum

from sqlalchemy import (
    BigInteger, String, Text, ForeignKey, DateTime,
    ARRAY, Enum, func, UniqueConstraint, Boolean
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    __allow_unmapped__ = True


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

    uploaded_files: List["File"] = relationship(back_populates="sender")
    pending_files: List["PendingFile"] = relationship(back_populates="sender")


class Vault(Base):
    __tablename__ = "vaults"

    vault_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_group_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), default="Shared Vault")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    files: List["File"] = relationship(
        back_populates="vault", cascade="all, delete-orphan"
    )


class Incident(Base):
    """Public event metadata — NO PII."""
    __tablename__ = "incidents"

    incident_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    incident_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    location_text: Mapped[Optional[str]] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text)
    tags: Mapped[Optional[list[str]]] = mapped_column(ARRAY(String), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    retained_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)


class Person(Base):
    """Pseudonymized person identity."""
    __tablename__ = "persons"

    person_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    person_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    encrypted_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name_nonce: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    is_minor: Mapped[bool] = mapped_column(Boolean, default=False)
    contact_info_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    contact_nonce: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    telegram_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    incidents: List["Incident"] = relationship(
        secondary="incident_persons",
        back_populates="persons"
    )


class IncidentPerson(Base):
    """Link table: incident ↔ person (many-to-many)."""
    __tablename__ = "incident_persons"

    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.incident_id", ondelete="CASCADE"),
        primary_key=True
    )
    person_id: Mapped[int] = mapped_column(
        ForeignKey("persons.person_id", ondelete="CASCADE"),
        primary_key=True
    )
    role_in_incident: Mapped[Optional[str]] = mapped_column(String(100))
    media_refs: Mapped[Optional[list[str]]] = mapped_column(ARRAY(String), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# Add relationship back-references
Incident.persons = relationship(
    "Person",
    secondary="incident_persons",
    back_populates="incidents"
)


class PendingFile(Base):
    """Files awaiting admin approval before being stored in the vault."""
    __tablename__ = "pending_files"

    pending_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sender_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE")
    )
    telegram_file_id: Mapped[str] = mapped_column(Text, nullable=False)
    telegram_file_unique_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    file_type: Mapped[FileType] = mapped_column(Enum(FileType), nullable=False)
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger)
    file_name: Mapped[Optional[str]] = mapped_column(String(255))
    caption: Mapped[Optional[str]] = mapped_column(Text)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    staging_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    cloud_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    incident_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    temp_person_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    reviewed_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    sender: "User" = relationship(back_populates="pending_files")


class File(Base):
    __tablename__ = "files"

    file_id_pk: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.incident_id", ondelete="CASCADE")
    )
    person_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("persons.person_id", ondelete="SET NULL"),
        nullable=True
    )
    telegram_file_id: Mapped[str] = mapped_column(Text, nullable=False)
    telegram_file_unique_id: Mapped[str] = mapped_column(
        Text, nullable=False, unique=True, index=True
    )
    cloud_key: Mapped[str] = mapped_column(String(255), nullable=False)
    vault_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_type: Mapped[FileType] = mapped_column(Enum(FileType), nullable=False)
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger)
    original_filename: Mapped[Optional[str]] = mapped_column(String(255))
    caption: Mapped[Optional[str]] = mapped_column(Text)
    exif_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_minor: Mapped[bool] = mapped_column(Boolean, default=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    retention_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)

    vault: "Vault" = relationship(back_populates="files")
    sender: "User" = relationship(back_populates="uploaded_files")


class AdminAuditLog(Base):
    """Admin action logs with 30-day retention."""
    __tablename__ = "admin_audit_logs"

    log_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    target_person_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("persons.person_id", ondelete="SET NULL"),
        nullable=True
    )
    target_file_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("files.file_id_pk", ondelete="SET NULL"),
        nullable=True
    )
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )