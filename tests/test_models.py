"""
Tests for SQLAlchemy Models — Evidence Vault System
"""
import pytest
from datetime import datetime
from uuid import uuid4

from models_vault import (
    Profile, Incident, Submission, DetectedPerson, 
    ConsentLog, AdminAuditLog, ProcessingJob, FaceEmbedding
)


class TestProfile:
    """Test Profile model."""
    
    def test_create_profile(self, db_session):
        """Test creating a new profile."""
        profile = Profile(
            telegram_user_id_hash="test_hash_123",
            encrypted_telegram_id="encrypted_id",
            telegram_id_nonce="nonce123",
            age_verified=True,
            accepted_consent_version="1.0"
        )
        db_session.add(profile)
        db_session.commit()
        
        assert profile.profile_id is not None
        assert profile.telegram_user_id_hash == "test_hash_123"
        assert profile.age_verified is True
        assert profile.accepted_consent_version == "1.0"
    
    def test_profile_unique_hash(self, db_session):
        """Test that telegram_user_id_hash is unique."""
        profile1 = Profile(telegram_user_id_hash="unique_hash")
        profile2 = Profile(telegram_user_id_hash="unique_hash")
        
        db_session.add(profile1)
        db_session.commit()
        
        db_session.add(profile2)
        with pytest.raises(Exception):  # IntegrityError
            db_session.commit()


class TestIncident:
    """Test Incident model."""
    
    def test_create_incident(self, db_session):
        """Test creating an incident."""
        incident = Incident(
            incident_type="police_misconduct",
            location_general="Connaught Place, Delhi",
            incident_date=datetime(2024, 1, 15, 14, 30),
            description_factual="Police vehicle ran red light",
            content_warning=False
        )
        db_session.add(incident)
        db_session.commit()
        
        assert incident.incident_id is not None
        assert incident.incident_type == "police_misconduct"


class TestSubmission:
    """Test Submission model."""
    
    def test_create_submission(self, db_session, sample_profile, sample_incident):
        """Test creating a submission."""
        from crypto_utils import generate_anonymous_token
        
        anonymous_token = generate_anonymous_token("abc123def456", "user_hash")
        
        submission = Submission(
            original_hash="abc123def456",
            uploader_anonymous_token=anonymous_token,
            incident_id=sample_incident.incident_id,
            file_type="video",
            file_size=1024000,
            exif_ref="r2://evidence-exif/abc123/exif.json"
        )
        db_session.add(submission)
        db_session.commit()
        
        assert submission.submission_id is not None
        assert submission.status == "pending_review"
        assert submission.uploader_anonymous_token == anonymous_token


class TestDetectedPerson:
    """Test DetectedPerson model."""
    
    def test_create_detected_person(self, db_session, sample_submission):
        """Test creating a detected person record."""
        detected = DetectedPerson(
            submission_id=sample_submission.submission_id,
            subject_type="civilian",
            blur_status="blurred",
            consent_status="none_requested",
            frame_index=10,
            bbox_x=100.0,
            bbox_y=200.0,
            bbox_width=150.0,
            bbox_height=180.0
        )
        db_session.add(detected)
        db_session.commit()
        
        assert detected.face_id is not None
        assert detected.blur_status == "blurred"
        assert detected.is_minor is False
    
    def test_mark_minor(self, db_session, sample_detected_person):
        """Test marking a face as minor."""
        sample_detected_person.is_minor = True
        db_session.commit()
        
        assert sample_detected_person.is_minor is True


class TestConsentLog:
    """Test ConsentLog model."""
    
    def test_log_consent(self, db_session, sample_submission, sample_detected_person):
        """Test logging a consent action."""
        log = ConsentLog(
            person_telegram_id_hash="user_hash_456",
            submission_id=sample_submission.submission_id,
            face_id=sample_detected_person.face_id,
            action="granted",
            consent_version="1.0",
            details={"method": "selfie_verification"}
        )
        db_session.add(log)
        db_session.commit()
        
        assert log.log_id is not None
        assert log.action == "granted"
        assert log.timestamp is not None


class TestAdminAuditLog:
    """Test AdminAuditLog model."""
    
    def test_log_admin_action(self, db_session, sample_submission):
        """Test logging an admin action."""
        audit = AdminAuditLog(
            admin_id=uuid4(),
            submission_id=sample_submission.submission_id,
            action="approve",
            reason="Approved for publication",
            metadata={"admin_user_id": 123456}
        )
        db_session.add(audit)
        db_session.commit()
        
        assert audit.log_id is not None
        assert audit.action == "approve"
        assert audit.timestamp is not None


class TestProcessingJob:
    """Test ProcessingJob model."""
    
    def test_create_job(self, db_session, sample_submission):
        """Test creating a processing job."""
        job = ProcessingJob(
            submission_id=sample_submission.submission_id,
            job_type="face_detection",
            status="queued",
            priority=5
        )
        db_session.add(job)
        db_session.commit()
        
        assert job.job_id is not None
        assert job.status == "queued"
        assert job.attempts == 0


class TestFaceEmbedding:
    """Test FaceEmbedding model."""
    
    def test_create_embedding(self, db_session, sample_profile):
        """Test creating a face embedding."""
        embedding = FaceEmbedding(
            profile_id=sample_profile.profile_id,
            embedding=b"\x00" * 128,  # Mock 128-dim embedding
            source_type="selfie"
        )
        db_session.add(embedding)
        db_session.commit()
        
        assert embedding.embedding_id is not None
        assert embedding.source_type == "selfie"


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def db_session():
    """Create a test database session."""
    from database import async_session_factory, async_engine
    from models_vault import Base
    
    # Create tables
    import asyncio
    async def setup():
        async with async_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    
    asyncio.run(setup())
    
    # Create session
    session = async_session_factory()
    try:
        yield session
    finally:
        session.close()
        
        # Cleanup
        async def teardown():
            async with async_engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
        
        asyncio.run(teardown())


@pytest.fixture
def sample_profile(db_session):
    """Create a sample profile for testing."""
    profile = Profile(
        telegram_user_id_hash="test_user_hash",
        age_verified=True,
        accepted_consent_version="1.0"
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    return profile


@pytest.fixture
def sample_incident(db_session, sample_profile):
    """Create a sample incident for testing."""
    incident = Incident(
        incident_type="test_incident",
        location_general="Test Location",
        incident_date=datetime(2024, 1, 1, 12, 0),
        created_by=sample_profile.profile_id
    )
    db_session.add(incident)
    db_session.commit()
    db_session.refresh(incident)
    return incident


@pytest.fixture
def sample_submission(db_session, sample_profile, sample_incident):
    """Create a sample submission for testing."""
    submission = Submission(
        original_hash="test_hash_123",
        uploader_id=sample_profile.profile_id,
        incident_id=sample_incident.incident_id,
        file_type="video",
        file_size=1024
    )
    db_session.add(submission)
    db_session.commit()
    db_session.refresh(submission)
    return submission


@pytest.fixture
def sample_detected_person(db_session, sample_submission):
    """Create a sample detected person for testing."""
    detected = DetectedPerson(
        submission_id=sample_submission.submission_id,
        subject_type="civilian",
        blur_status="blurred",
        consent_status="none_requested"
    )
    db_session.add(detected)
    db_session.commit()
    db_session.refresh(detected)
    return detected