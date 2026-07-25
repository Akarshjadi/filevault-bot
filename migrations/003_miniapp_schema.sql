-- Mini App Schema Migration
-- Simplified schema for privacy-first WebApp submissions
-- No originals bucket, no identity linkage, automated verification only

-- 1. Mini App submissions table
CREATE TABLE IF NOT EXISTS webapp_submissions (
    submission_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_hash VARCHAR(64) NOT NULL UNIQUE,
    uploader_anonymous_token VARCHAR(64) NOT NULL,
    
    -- Incident metadata
    incident_type VARCHAR(50) NOT NULL,
    location_general TEXT,
    incident_date TIMESTAMPTZ,
    description_factual TEXT,
    content_warning BOOLEAN DEFAULT FALSE,
    official_tag_count INTEGER DEFAULT 0,
    
    -- File metadata
    file_type VARCHAR(20) NOT NULL,
    file_size BIGINT,
    r2_key VARCHAR(255) NOT NULL,
    
    -- Verification
    verification_status VARCHAR(50) DEFAULT 'pending',
    verification_details JSONB,
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Indexes
CREATE INDEX IF NOT EXISTS idx_webapp_submissions_hash ON webapp_submissions(file_hash);
CREATE INDEX IF NOT EXISTS idx_webapp_submissions_token ON webapp_submissions(uploader_anonymous_token);
CREATE INDEX IF NOT EXISTS idx_webapp_submissions_status ON webapp_submissions(verification_status);
CREATE INDEX IF NOT EXISTS idx_webapp_submissions_date ON webapp_submissions(created_at);

-- 3. Trigger for updated_at
CREATE OR REPLACE FUNCTION update_webapp_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_webapp_submissions_updated_at 
    BEFORE UPDATE ON webapp_submissions 
    FOR EACH ROW EXECUTE FUNCTION update_webapp_updated_at();

-- 4. Record migration
INSERT INTO schema_migrations (version) VALUES ('003_miniapp_schema') ON CONFLICT DO NOTHING;