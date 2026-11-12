"""
SQLAlchemy 2.0 Models — Evidence Vault System
DPDP Act 2023 Compliant Citizen Documentation Platform
"""
from datetime import datetime
from typing import Optional, List
from enum import Enum as PyEnum

from sqlalchemy import (
    BigInteger, String, Text, ForeignKey, DateTime,
    ARRAY, Enum, func, UniqueConstraint, Boolean, Float, Interval
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, BYTEA
import uuid


class Base(DeclarativeBase):
    __allow_unmapped__ = True


class SubjectType(str, PyEnum):
    CIVILIAN = "civilian"
    OFFICIAL = "official"
    UPLOADER_SELF = "uploader_self"


class BlurStatus(str, PyEnum):
    BLURRED = "blurred"
    UNBLURRED = "unblurred"


class ConsentStatus(str, PyEnum):
    NONE_REQUESTED = "none_requested"
    REQUESTED = "requested"
    GRANTED = "granted"
    DENIED = "denied"
    REVOKED = "revoked"


class VerificationStatus(str, PyEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    FAILED = "failed"


class SubmissionStatus(str, PyEnum):
    PENDING_REVIEW = "pending_review"
    PROCESSING = "processing"
    PROCESSED = "processed"
    PUBLISHED = "published"
    REJECTED = "rejected"


class JobStatus(str, PyEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class SourceType(str, PyEnum):
    SELFIE = "selfie"
    ID_DOCUMENT = "id_document"
    MANUAL = "manual"


class Profile(Base):
    """User profiles with consent tracking and age verification."""
    __tablename__ = "profiles"

    profile_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    telegram_user_id_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    encrypted_telegram_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    telegram_id_nonce: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    age_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    age_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_consent_version: Mapped[str] = mapped_column(String(50), nullable=False, default='1.0')
    consent_accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    face_reference_embedding: Mapped[Optional[bytes]] = mapped_column(BYTEA, nullable=True)
    face_reference_uploaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    incidents_created: List["Incident"] = relationship("Incident", back_populates="created_by")
    face_embeddings: List["FaceEmbedding"] = relationship("FaceEmbedding", back_populates="profile", cascade="all, delete-orphan")
    detected_persons: List["DetectedPerson"] = relationship("DetectedPerson", back_populates="consent_holder")


class Incident(Base):
    """Public incident metadata — NO PII."""
    __tablename__ = "incidents"

    incident_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incident_type: Mapped[str] = mapped_column(String(50), nullable=False)
    location_general: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    incident_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    description_factual: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content_warning: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.profile_id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    created_by_profile: Mapped[Optional["Profile"]] = relationship("Profile", back_populates="incidents_created")
    submissions: List["Submission"] = relationship("Submission", back_populates="incident")


class Submission(Base):
    """File submissions linking to incidents."""
    __tablename__ = "submissions"

    submission_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    original_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    uploader_anonymous_token: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    incident_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("incidents.incident_id", ondelete="SET NULL"), nullable=True, index=True)
    server_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    exif_ref: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    verification_status: Mapped[str] = mapped_column(String(50), default="unverified")
    status: Mapped[str] = mapped_column(String(50), default="pending_review", index=True)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    processing_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    incident: Mapped[Optional["Incident"]] = relationship("Incident", back_populates="submissions")
    detected_persons: List["DetectedPerson"] = relationship("DetectedPerson", back_populates="submission", cascade="all, delete-orphan")
    consent_logs: List["ConsentLog"] = relationship("ConsentLog", back_populates="submission", cascade="all, delete-orphan")
    processing_jobs: List["ProcessingJob"] = relationship("ProcessingJob", back_populates="submission")
    admin_audit_logs: List["AdminAuditLog"] = relationship("AdminAuditLog", back_populates="submission")


class DetectedPerson(Base):
    """Face detection results and consent tracking."""
    __tablename__ = "detected_persons"

    face_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("submissions.submission_id", ondelete="CASCADE"), nullable=False, index=True)
    subject_type: Mapped[str] = mapped_column(String(50), nullable=False)
    blur_status: Mapped[str] = mapped_column(String(50), default="blurred")
    consent_status: Mapped[str] = mapped_column(String(50), default="none_requested")
    consent_requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    consent_resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_minor: Mapped[bool] = mapped_column(Boolean, default=False)
    face_embedding: Mapped[Optional[bytes]] = mapped_column(BYTEA, nullable=True)
    frame_index: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    timestamp_in_video: Mapped[Optional[datetime]] = mapped_column(Interval, nullable=True)
    bbox_x: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bbox_y: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bbox_width: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bbox_height: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    official_category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    official_badge_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    admin_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_telegram_id_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    submission: Mapped["Submission"] = relationship("Submission", back_populates="detected_persons")
    consent_holder: Mapped[Optional["Profile"]] = relationship("Profile", back_populates="detected_persons")
    consent_logs: List["ConsentLog"] = relationship("ConsentLog", back_populates="face")


class ConsentLog(Base):
    """Audit trail for all consent actions."""
    __tablename__ = "consent_log"

    log_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_telegram_id_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    submission_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("submissions.submission_id", ondelete="CASCADE"), nullable=False, index=True)
    face_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("detected_persons.face_id", ondelete="CASCADE"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    consent_version: Mapped[str] = mapped_column(String(50), nullable=False)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    submission: Mapped["Submission"] = relationship("Submission", back_populates="consent_logs")
    face: Mapped["DetectedPerson"] = relationship("DetectedPerson", back_populates="consent_logs")


class AdminAuditLog(Base):
    """Admin action logs with DPDP compliance."""
    __tablename__ = "admin_audit_log"

    log_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.profile_id", ondelete="SET NULL"), nullable=True, index=True)
    submission_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("submissions.submission_id", ondelete="CASCADE"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    metadata: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    submission: Mapped[Optional["Submission"]] = relationship("Submission", back_populates="admin_audit_logs")


class ProcessingJob(Base):
    """Background processing jobs queue."""
    __tablename__ = "processing_jobs"

    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("submissions.submission_id", ondelete="CASCADE"), nullable=False, index=True)
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="queued", index=True)
    priority: Mapped[int] = mapped_column(BigInteger, default=0)
    attempts: Mapped[int] = mapped_column(BigInteger, default=0)
    max_attempts: Mapped[int] = mapped_column(BigInteger, default=3)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    submission: Mapped["Submission"] = relationship("Submission", back_populates="processing_jobs")


# Session helper for backwards compatibility
from database import async_session_factory


async def get_session():
    """Get an async database session."""
    async with async_session_factory() as session:
        yield session


class FaceEmbedding(Base):
    """Face embeddings for uploader self-matching."""
    __tablename__ = "face_embeddings"

    embedding_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.profile_id", ondelete="CASCADE"), nullable=False, index=True)
    embedding: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    # Relationships
    profile: Mapped["Profile"] = relationship("Profile", back_populates="face_embeddings")