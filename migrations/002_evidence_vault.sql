-- Evidence Vault Migration — DPDP Act 2023 Compliant
-- Adds citizen documentation system with face blur, consent tracking, and R2 storage

-- 1. Profiles table (Telegram account links, consent tracking)
CREATE TABLE IF NOT EXISTS profiles (
    profile_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_user_id_hash VARCHAR(64) NOT NULL UNIQUE,
    encrypted_telegram_id TEXT,
    telegram_id_nonce VARCHAR(24),
    age_verified BOOLEAN DEFAULT FALSE,
    age_verified_at TIMESTAMPTZ,
    accepted_consent_version VARCHAR(50) NOT NULL DEFAULT '1.0',
    consent_accepted_at TIMESTAMPTZ DEFAULT NOW(),
    face_reference_embedding BYTEA,
    face_reference_uploaded_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Incidents table (public incident metadata)
CREATE TABLE IF NOT EXISTS incidents (
    incident_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_type VARCHAR(50) NOT NULL,
    location_general TEXT,
    incident_date TIMESTAMPTZ NOT NULL,
    description_factual TEXT,
    content_warning BOOLEAN DEFAULT FALSE,
    created_by UUID REFERENCES profiles(profile_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. Submissions table (file submissions linking to incidents)
-- ANONYMITY: No persistent uploader identity linkage
CREATE TABLE IF NOT EXISTS submissions (
    submission_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    original_hash VARCHAR(64) NOT NULL UNIQUE,
    uploader_anonymous_token VARCHAR(64) NOT NULL,  -- One-way token, not reversible to identity
    incident_id UUID REFERENCES incidents(incident_id) ON DELETE SET NULL,
    server_timestamp TIMESTAMPTZ DEFAULT NOW(),
    exif_ref TEXT,
    verification_status VARCHAR(50) DEFAULT 'unverified',
    status VARCHAR(50) DEFAULT 'pending_review',
    file_type VARCHAR(20) NOT NULL,
    file_size BIGINT,
    processing_started_at TIMESTAMPTZ,
    processing_completed_at TIMESTAMPTZ,
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Detected persons table (face detection results and consent tracking)
CREATE TABLE IF NOT EXISTS detected_persons (
    face_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    submission_id UUID REFERENCES submissions(submission_id) ON DELETE CASCADE,
    subject_type VARCHAR(50) NOT NULL CHECK (subject_type IN ('civilian', 'official', 'uploader_self')),
    blur_status VARCHAR(50) DEFAULT 'blurred' CHECK (blur_status IN ('blurred', 'unblurred')),
    consent_status VARCHAR(50) DEFAULT 'none_requested' CHECK (consent_status IN ('none_requested', 'requested', 'granted', 'denied', 'revoked')),
    consent_requested_at TIMESTAMPTZ,
    consent_resolved_at TIMESTAMPTZ,
    is_minor BOOLEAN DEFAULT FALSE,
    face_embedding BYTEA,
    frame_index INTEGER,
    timestamp_in_video INTERVAL,
    bbox_x FLOAT,
    bbox_y FLOAT,
    bbox_width FLOAT,
    bbox_height FLOAT,
    official_category VARCHAR(100),
    official_badge_number VARCHAR(100),
    admin_approved BOOLEAN DEFAULT FALSE,
    consent_telegram_id_hash VARCHAR(64),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. Consent log (audit trail for all consent actions)
CREATE TABLE IF NOT EXISTS consent_log (
    log_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_telegram_id_hash VARCHAR(64) NOT NULL,
    submission_id UUID REFERENCES submissions(submission_id) ON DELETE CASCADE,
    face_id UUID REFERENCES detected_persons(face_id) ON DELETE CASCADE,
    action VARCHAR(50) NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    consent_version VARCHAR(50) NOT NULL,
    ip_address INET,
    user_agent TEXT,
    details JSONB
);

-- 6. Admin audit log (enhanced from existing)
CREATE TABLE IF NOT EXISTS admin_audit_log (
    log_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_id UUID REFERENCES profiles(profile_id) ON DELETE SET NULL,
    submission_id UUID REFERENCES submissions(submission_id) ON DELETE CASCADE,
    action VARCHAR(50) NOT NULL,
    reason TEXT,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB,
    ip_address INET,
    user_agent TEXT
);

-- 7. Processing jobs queue (for tracking background tasks)
CREATE TABLE IF NOT EXISTS processing_jobs (
    job_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    submission_id UUID REFERENCES submissions(submission_id) ON DELETE CASCADE,
    job_type VARCHAR(50) NOT NULL,
    status VARCHAR(50) DEFAULT 'queued',
    priority INTEGER DEFAULT 0,
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    error_message TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 8. Face embeddings table (for uploader self-matching)
CREATE TABLE IF NOT EXISTS face_embeddings (
    embedding_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id UUID REFERENCES profiles(profile_id) ON DELETE CASCADE,
    embedding BYTEA NOT NULL,
    source_type VARCHAR(50) NOT NULL CHECK (source_type IN ('selfie', 'id_document', 'manual')),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- INDEXES
-- ============================================================================

-- Profiles indexes
CREATE INDEX IF NOT EXISTS idx_profiles_telegram_hash ON profiles(telegram_user_id_hash);
CREATE INDEX IF NOT EXISTS idx_profiles_consent_version ON profiles(accepted_consent_version);

-- Submissions indexes
CREATE INDEX IF NOT EXISTS idx_submissions_uploader ON submissions(uploader_id);
CREATE INDEX IF NOT EXISTS idx_submissions_incident ON submissions(incident_id);
CREATE INDEX IF NOT EXISTS idx_submissions_status ON submissions(status);
CREATE INDEX IF NOT EXISTS idx_submissions_hash ON submissions(original_hash);
CREATE INDEX IF NOT EXISTS idx_submissions_created_at ON submissions(created_at);

-- Detected persons indexes
CREATE INDEX IF NOT EXISTS idx_detected_persons_submission ON detected_persons(submission_id);
CREATE INDEX IF NOT EXISTS idx_detected_persons_consent ON detected_persons(consent_status);
CREATE INDEX IF NOT EXISTS idx_detected_persons_blur ON detected_persons(blur_status);
CREATE INDEX IF NOT EXISTS idx_detected_persons_type ON detected_persons(subject_type);
CREATE INDEX IF NOT EXISTS idx_detected_persons_minor ON detected_persons(is_minor);
CREATE INDEX IF NOT EXISTS idx_detected_persons_telegram_hash ON detected_persons(consent_telegram_id_hash);

-- Consent log indexes
CREATE INDEX IF NOT EXISTS idx_consent_log_person ON consent_log(person_telegram_id_hash);
CREATE INDEX IF NOT EXISTS idx_consent_log_submission ON consent_log(submission_id);
CREATE INDEX IF NOT EXISTS idx_consent_log_face ON consent_log(face_id);
CREATE INDEX IF NOT EXISTS idx_consent_log_timestamp ON consent_log(timestamp);

-- Admin audit log indexes
CREATE INDEX IF NOT EXISTS idx_admin_audit_admin ON admin_audit_log(admin_id);
CREATE INDEX IF NOT EXISTS idx_admin_audit_submission ON admin_audit_log(submission_id);
CREATE INDEX IF NOT EXISTS idx_admin_audit_timestamp ON admin_audit_log(timestamp);

-- Processing jobs indexes
CREATE INDEX IF NOT EXISTS idx_processing_jobs_submission ON processing_jobs(submission_id);
CREATE INDEX IF NOT EXISTS idx_processing_jobs_status ON processing_jobs(status);
CREATE INDEX IF NOT EXISTS idx_processing_jobs_priority ON processing_jobs(priority, created_at);

-- Face embeddings index
CREATE INDEX IF NOT EXISTS idx_face_embeddings_profile ON face_embeddings(profile_id);

-- Incidents indexes
CREATE INDEX IF NOT EXISTS idx_incidents_date ON incidents(incident_date);
CREATE INDEX IF NOT EXISTS idx_incidents_type ON incidents(incident_type);
CREATE INDEX IF NOT EXISTS idx_incidents_created_by ON incidents(created_by);

-- ============================================================================
-- TRIGGERS FOR UPDATED_AT
-- ============================================================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply trigger to tables with updated_at
CREATE TRIGGER update_profiles_updated_at BEFORE UPDATE ON profiles
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_incidents_updated_at BEFORE UPDATE ON incidents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_submissions_updated_at BEFORE UPDATE ON submissions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_detected_persons_updated_at BEFORE UPDATE ON detected_persons
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_processing_jobs_updated_at BEFORE UPDATE ON processing_jobs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- FUNCTIONS
-- ============================================================================

-- Function to hash telegram user IDs consistently
CREATE OR REPLACE FUNCTION hash_telegram_id(user_id BIGINT)
RETURNS VARCHAR(64) AS $$
BEGIN
    RETURN encode(digest(user_id::TEXT || (SELECT key_hash FROM encryption_keys WHERE key_name = 'master_salt'), 'sha256'), 'hex');
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Function to clean up expired processing jobs
CREATE OR REPLACE FUNCTION cleanup_old_processing_jobs(retention_days INTEGER DEFAULT 30)
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM processing_jobs
    WHERE status IN ('completed', 'failed')
      AND updated_at < NOW() - INTERVAL '1 day' * retention_days
    RETURNING COUNT(*) INTO deleted_count;
    
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- RECORD MIGRATION
-- ============================================================================

INSERT INTO schema_migrations (version) VALUES ('002_evidence_vault') ON CONFLICT DO NOTHING;