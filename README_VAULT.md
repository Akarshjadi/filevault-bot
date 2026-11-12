# FileVault Evidence Vault — Implementation Summary

## System Overview

Complete Telegram bot system for citizen documentation of public incidents, built for **DPDP Act 2023 compliance** with **maximum uploader anonymity**.

## What Was Built

### Core Infrastructure
- **Database Migration** (`migrations/002_evidence_vault.sql`): 8 tables with UUID keys, indexes, triggers
- **Models** (`models_vault.py`): SQLAlchemy 2.0 models for all vault entities
- **Storage** (`storage/r2.py`): Multi-bucket Cloudflare R2 with 4 separate buckets
- **Processing** (`processing/face_blur.py`, `processing/generate_preview.py`): MediaPipe face detection, blurring, preview generation

### Bot Handlers
- **Registration** (`handlers/register.py`): Consent-acceptance, age verification
- **Submissions** (`handlers/submit.py`): Anonymous evidence upload with EXIF stripping
- **Consent** (`handlers/consent.py`): Selfie verification, face tagging, consent flows
- **Admin** (`handlers/admin.py`): Review queue, face-by-face approval, minor marking
- **Right to be Forgotten** (`handlers/forget.py`): Complete data deletion

### Background Processing
- **Task Queue** (`tasks.py`): Async workers for face detection, consent updates, publishing

### Security & Anonymity
- **Anonymous Tokens**: HMAC-based one-way tokens replace uploader identity
- **EXIF Stripping**: All metadata removed before storage
- **No Identity Linkage**: Submissions cannot be traced to uploaders

## Key Files

```
migrations/002_evidence_vault.sql    # Database schema
models_vault.py                      # SQLAlchemy models
storage/r2.py                        # R2 multi-bucket storage
processing/face_blur.py              # Face detection & blur
processing/generate_preview.py       # Preview generation
handlers/
  ├── register.py                    # User registration
  ├── submit.py                      # Anonymous submissions
  ├── consent.py                     # Consent management
  ├── admin.py                       # Admin review
  └── forget.py                      # Data deletion
tasks.py                             # Background job queue
bot.py                               # Main entry point
crypto_utils.py                      # Encryption + anonymous tokens
ANONYMITY.md                         # Complete anonymity design doc
requirements_vault.txt               # Dependencies
tests/test_models.py                 # Model tests
```

## Installation

1. **Install dependencies:**
   ```bash
   pip install -r requirements_vault.txt
   ```

2. **Configure environment:**
   ```bash
   cp .env.example .env
   # Fill in:
   # - BOT_TOKEN (Telegram)
   # - DATABASE_URL (PostgreSQL)
   # - S3_ENDPOINT_URL (Cloudflare R2)
   # - S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY
   # - ENCRYPTION_MASTER_SALT (64-char hex)
   # - ADMIN_IDS (comma-separated Telegram IDs)
   ```

3. **Run migrations:**
   ```bash
   python bot.py
   # Migrations run automatically on startup
   ```

4. **Start bot:**
   ```bash
   python bot.py
   ```

## Architecture Decisions

### Anonymity-First Design

**Why anonymous tokens instead of user IDs?**

The system treats uploader identity as a **liability**, not an asset. The HMAC-based `uploader_anonymous_token` allows:
- Uploaders to retrieve their submissions
- Rate limiting (anti-spam)
- **Zero possibility** of identity correlation

See `ANONYMITY.md` for full threat model and design rationale.

### Multi-Bucket R2 Storage

```
evidence-originals/   → Private, 90-day retention
evidence-exif/        → Private, 1-year retention  
evidence-processing/  → Private, temporary
evidence-published/   → Private, long-term
```

Each bucket has different access controls and lifecycle policies.

### Face Blur by Default

All faces are blurred immediately upon upload. Unblurring requires:
1. Explicit consent (self or third-party)
2. Admin approval (for officials)
3. Minors: **Never unblurred** (legal requirement override)

## Database Schema

### Key Tables

**profiles** - User identities (encrypted, minimal)
- `telegram_user_id_hash` (unique, hashed)
- `age_verified` (boolean)
- `accepted_consent_version`

**submissions** - Evidence files (anonymous)
- `original_hash` (SHA-256)
- `uploader_anonymous_token` (HMAC, one-way)
- `incident_id` (linked)
- `status` (pending_review/processed/published/rejected)

**detected_persons** - Face detection results
- `face_id` (UUID)
- `submission_id` (FK)
- `subject_type` (civilian/official/uploader_self)
- `blur_status` (blurred/unblurred)
- `consent_status` (none_requested/requested/granted/denied/revoked)
- `is_minor` (boolean, permanent blur)

**consent_log** - Consent audit trail
- All consent actions logged with timestamp, version, IP

**admin_audit_log** - Admin actions
- All admin decisions logged with reason

## Security Features

1. **Uploader Anonymity**: No persistent identity linkage
2. **EXIF Stripping**: All metadata removed from images
3. **Face Blur**: Default for all faces
4. **Consent Required**: Silence ≠ consent
5. **Minor Protection**: Never unblurred (DPDP requirement)
6. **Audit Logging**: Complete trail of admin/consent actions
7. **Right to be Forgotten**: Complete data deletion
8. **Encryption**: AES-256-GCM for sensitive fields
9. **Hashing**: SHA-256 with master salt for indexing

## Testing

```bash
# Run model tests
pytest tests/test_models.py -v

# Test storage
python scripts/test_storage.py
```

## Production Deployment

### Environment Variables
```bash
BOT_TOKEN=...
DATABASE_URL=postgresql+asyncpg://...
S3_ENDPOINT_URL=https://...
R2_BUCKET_ORIGINALS=evidence-originals
R2_BUCKET_EXIF=evidence-exif
R2_BUCKET_PROCESSING=evidence-processing
R2_BUCKET_PUBLISHED=evidence-published
S3_ACCESS_KEY_ID=...
S3_SECRET_ACCESS_KEY=...
ENCRYPTION_MASTER_SALT=64_char_hex_salt
ADMIN_IDS=123456789,987654321
```

### Scaling Considerations

- **Task Queue**: Currently asyncio, migrate to Celery + Redis for production
- **Database**: Connection pooling already configured for Railway/Supabase
- **R2**: Multipart upload for large files (>8MB)
- **Face Detection**: CPU-based MediaPipe, consider GPU for high volume

## Compliance Matrix

| DPDP Act 2023 Requirement | Implementation |
|---------------------------|----------------|
| Right to be forgotten | `/forget` command, complete deletion |
| Data minimization | Minimal fields, anonymous tokens |
| Purpose limitation | Only incident documentation |
| Consent management | Versioned consent, explicit opt-in |
| Minor protection | `is_minor` flag, permanent blur |
| Transparency | Clear privacy policy in `/start` |
| Security | Encryption, hashing, audit logs |

## Next Steps

1. **Testing**: Add integration tests for full submission flow
2. **Monitoring**: Add Prometheus metrics for queue depth, processing time
3. **Rate Limiting**: Implement per-user submission limits (in-memory)
4. **Admin UI**: Consider web dashboard for easier review
5. **Backup**: R2 cross-region replication for disaster recovery

## Support

For issues or questions, refer to:
- `ANONYMITY.md` - Complete anonymity design rationale
- `SETUP_GUIDE.md` - Detailed setup instructions
- Inline code documentation

---

**Built with safety as the primary constraint.**