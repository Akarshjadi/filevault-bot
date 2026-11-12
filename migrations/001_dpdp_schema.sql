-- DPDP Act 2023 Compliant Schema Migration
-- Adds pseudonymization, encryption support, minor protection, and retention

-- 1. Encryption key management
CREATE TABLE IF NOT EXISTS encryption_keys (
    key_id SERIAL PRIMARY KEY,
    key_name VARCHAR(50) UNIQUE NOT NULL,
    key_hash VARCHAR(64) NOT NULL,
    rotated_at TIMESTAMPTZ DEFAULT NOW(),
    is_active BOOLEAN DEFAULT TRUE
);

-- Insert initial master salt placeholder (fill in real salt via app)
INSERT INTO encryption_keys (key_name, key_hash) VALUES ('master_salt', 'PLACEHOLDER') ON CONFLICT DO NOTHING;

-- 2. Incidents table (public event metadata, NO PII)
CREATE TABLE IF NOT EXISTS incidents (
    incident_id SERIAL PRIMARY KEY,
    incident_date DATE NOT NULL,
    location_text TEXT,
    description TEXT,
    tags TEXT[],
    created_at TIMESTAMPTZ DEFAULT NOW(),
    retained_until TIMESTAMPTZ DEFAULT NOW() + INTERVAL '1 year',
    is_archived BOOLEAN DEFAULT FALSE
);

-- 3. Persons table (pseudonymized identities)
CREATE TABLE IF NOT EXISTS persons (
    person_id SERIAL PRIMARY KEY,
    person_hash VARCHAR(64) UNIQUE NOT NULL,
    encrypted_name TEXT,
    name_nonce VARCHAR(24),
    is_minor BOOLEAN DEFAULT FALSE,
    contact_info_encrypted TEXT,
    contact_nonce VARCHAR(24),
    telegram_user_id BIGINT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Incident-Person link table
CREATE TABLE IF NOT EXISTS incident_persons (
    incident_id INT REFERENCES incidents(incident_id) ON DELETE CASCADE,
    person_id INT REFERENCES persons(person_id) ON DELETE CASCADE,
    role_in_incident VARCHAR(100),
    media_refs TEXT[],
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (incident_id, person_id)
);

-- 5. New files table
CREATE TABLE IF NOT EXISTS files_new (
    file_id_pk SERIAL PRIMARY KEY,
    incident_id INT REFERENCES incidents(incident_id) ON DELETE CASCADE,
    person_id INT REFERENCES persons(person_id) ON DELETE SET NULL,
    telegram_file_id TEXT NOT NULL,
    telegram_file_unique_id TEXT UNIQUE NOT NULL,
    cloud_key TEXT NOT NULL,
    vault_message_id BIGINT,
    file_type VARCHAR(20),
    file_size BIGINT,
    original_filename TEXT,
    caption TEXT,
    exif_data JSONB,
    is_minor BOOLEAN DEFAULT FALSE,
    uploaded_at TIMESTAMPTZ DEFAULT NOW(),
    retention_expires_at TIMESTAMPTZ DEFAULT NOW() + INTERVAL '1 year'
);

-- Migrate from old files table if it exists
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'files_legacy') THEN
        -- Already migrated
        RETURN;
    END IF;
    
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'files') THEN
        ALTER TABLE files RENAME TO files_legacy;
        ALTER TABLE files_new RENAME TO files;
    END IF;
END $$;

-- 6. Admin audit logs (30-day retention)
CREATE TABLE IF NOT EXISTS admin_audit_logs (
    log_id SERIAL PRIMARY KEY,
    admin_id BIGINT NOT NULL,
    action VARCHAR(50) NOT NULL,
    target_person_id INT REFERENCES persons(person_id),
    target_file_id INT REFERENCES files(file_id_pk),
    ip_address INET,
    user_agent TEXT,
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 7. Indexes for retention and performance
CREATE INDEX IF NOT EXISTS idx_files_retention ON files(retention_expires_at) WHERE is_archived = FALSE;
CREATE INDEX IF NOT EXISTS idx_incidents_retention ON incidents(retained_until) WHERE is_archived = FALSE;
CREATE INDEX IF NOT EXISTS idx_audit_logs_cleanup ON admin_audit_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_persons_hash ON persons(person_hash);
CREATE INDEX IF NOT EXISTS idx_files_unique_id ON files(telegram_file_unique_id);

-- 8. Update pending_files to reference incidents
ALTER TABLE pending_files ADD COLUMN IF NOT EXISTS incident_id INT;
ALTER TABLE pending_files ADD CONSTRAINT IF NOT EXISTS fk_pending_incident 
    FOREIGN KEY (incident_id) REFERENCES incidents(incident_id) ON DELETE SET NULL;

-- 9. Add person_name_temp for admin review (not permanent)
ALTER TABLE pending_files ADD COLUMN IF NOT EXISTS temp_person_name TEXT;

-- 10. Enable pgcrypto for additional encryption if needed
CREATE EXTENSION IF NOT EXISTS pgcrypto;