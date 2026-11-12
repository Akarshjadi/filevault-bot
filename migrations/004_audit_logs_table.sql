-- Audit Logs Table Migration
-- User action audit logs with 30-day retention
-- Tracks user-initiated actions (uploads, deletes, tags, GDPR forget, etc.)

CREATE TABLE IF NOT EXISTS audit_logs (
    log_id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    action VARCHAR(50) NOT NULL,
    file_id BIGINT,
    details TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for query performance
CREATE INDEX IF NOT EXISTS idx_audit_user_id ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action);
CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_logs(created_at);

-- Record migration
INSERT INTO schema_migrations (version) VALUES ('004_audit_logs_table') ON CONFLICT DO NOTHING;
