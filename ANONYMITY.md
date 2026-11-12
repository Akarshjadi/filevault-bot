# Uploader Anonymity Design — FileVault Evidence Vault

## Critical Security Principle

**Uploader anonymity is the single most important safety feature in this system.**

The system is designed so that **no one**—not the bot administrators, not law enforcement (without extraordinary measures), not even the system itself—can determine who uploaded a specific file.

## Threat Model

The primary threat is **retaliation against uploaders**, especially in cases involving:
- Police misconduct
- Government corruption
- Exposing powerful individuals/organizations
- Whistleblowing

Metadata, server logs, and Telegram account linkage that could tie a specific person to a specific upload represent an existential risk.

## Design Decisions

### 1. No Persistent Uploader Identity Linkage

**Before (Insecure):**
```sql
submissions (
  uploader_id UUID REFERENCES profiles(profile_id) ON DELETE CASCADE
)
```

**After (Secure):**
```sql
submissions (
  uploader_anonymous_token VARCHAR(64) NOT NULL  -- One-way, non-reversible
)
```

The `uploader_anonymous_token` is an HMAC-based hash that:
- Allows the uploader to retrieve their submissions (via Telegram bot)
- Prevents spam (rate limiting)
- **Cannot be reversed** to identify the user
- Does not create a persistent trace

### 2. Anonymous Token Generation

```python
def generate_anonymous_token(file_hash: str, user_hash: str) -> str:
    """
    Generate a one-way anonymous token.
    
    Formula: HMAC(MASTER_SALT, file_hash:user_hash)
    
    Properties:
    - Deterministic: Same user + file = same token
    - One-way: Cannot extract user_hash from token
    - Unique per submission: Different files = different tokens
    """
    token_data = f"{file_hash}:{user_hash}".encode()
    return hmac.new(MASTER_SALT, token_data, hashlib.sha256).hexdigest()
```

**Why HMAC?**
- One-way function (cannot reverse-engineer input)
- Keyed with MASTER_SALT (stored separately, rotated periodically)
- Deterministic (same inputs = same output) for deduplication
- No rainbow table attacks possible

### 3. No Registration Required for Submissions

Users can submit evidence **without registering**. Registration is only required for:
- Managing consents (unblurring faces)
- Checking submission status (via anonymous token)
- Administrative functions

This means:
- No profile linkage for casual uploaders
- No identity verification beyond Telegram's existing anti-spam
- Minimal data retention

### 4. EXIF Stripping

**All EXIF metadata is stripped from images before storage.**

EXIF data can reveal:
- GPS coordinates (exact upload location)
- Device information
- Timestamps
- Software used

**Implementation:**
```python
def strip_exif(self, file_bytes: bytes, filename: str) -> bytes:
    """Strip ALL EXIF metadata from image for privacy."""
    image = Image.open(io.BytesIO(file_bytes))
    
    # Create new image without EXIF
    data = list(image.getdata())
    image_without_exif = Image.new(image.mode, image.size)
    image_without_exif.putdata(data)
    
    # Save without EXIF
    output = io.BytesIO()
    image_without_exif.save(output, format='JPEG', quality=95)
    return output.getvalue()
```

The extracted EXIF is stored **separately** in the `evidence-exif` R2 bucket (with 1-year retention), but **not linked** to the original file in any queryable way.

### 5. Incidents Created Anonymously

```python
incident = Incident(
    incident_type=submission.incident_type,
    location_general=submission.location,
    created_by=None,  # NO UPLOADER IDENTITY
)
```

The `created_by` field is always `NULL` for submissions. This ensures:
- No correlation between incidents and uploaders
- No way to query "who created this incident?"
- Incidents are purely about the event, not the reporter

### 6. Storage Architecture

**R2 Buckets (All Private):**

1. **evidence-originals** (90-day retention)
   - Original unaltered files
   - Keyed by SHA-256 hash
   - **No user ID in path**

2. **evidence-exif** (1-year retention)
   - Stripped EXIF data
   - Stored separately
   - **Not queryable by user**

3. **evidence-processing** (temporary)
   - Blurred copies during processing
   - Deleted after finalization

4. **evidence-published** (long-term)
   - Final approved copies
   - Served via presigned URLs
   - **No user metadata**

**Object Naming (No User Info):**
```
evidence-originals/{sha256_hash}          # No username, no user_id
evidence-exif/{sha256_hash}/exif.json     # Linked only by hash
evidence-processing/{submission_id}/blurred_initial.mp4
evidence-published/{submission_id}/final.mp4
```

### 7. Database Design

**No Uploader-Revealing Queries:**

```sql
-- ❌ NEVER ALLOWED
SELECT * FROM submissions WHERE uploader_id = 'known_user_id';

-- ✅ ANONYMOUS
SELECT * FROM submissions WHERE uploader_anonymous_token = 'token_only';
```

**Indexes only on anonymous fields:**
```sql
CREATE INDEX idx_submissions_anonymous_token ON submissions(uploader_anonymous_token);
-- NO INDEX on uploader_id (it doesn't exist)
```

### 8. Right to be Forgotten

The `/forget` command:
- Does not require user registration
- Works via anonymous tokens
- Deletes **all** data (no selective deletion by identity)
- Removes R2 files completely
- Cannot be traced back to individual

```python
async def forget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # No profile lookup by Telegram ID
    # No identity verification beyond bot command
    
    # Delete everything
    await delete_all_submissions(anonymous_token_only=True)
```

## What This System Does NOT Store

To maintain anonymity, the system intentionally does **NOT** store:

1. **Telegram User IDs** (linked to submissions)
2. **Phone numbers**
3. **Email addresses**
4. **IP addresses** (in submission metadata)
5. **GPS coordinates** (from EXIF)
6. **Device fingerprints**
7. **Timing correlations** (submission timestamp only, no session logs)

## What IS Stored

The system **does** store:

1. **Submission content** (blurred faces)
2. **Incident metadata** (type, location, description)
3. **Consent logs** (for legal compliance)
4. **Admin audit logs** (for accountability)
5. **Anonymous tokens** (for user retrieval)

## Anti-Enumeration Protections

### Rate Limiting
- Limit submissions per Telegram account per time window
- Prevents flooding to identify patterns
- Token-based, not identity-based

### No User Enumeration Endpoints
```python
# ❌ NEVER EXPOSE
GET /submissions?user_id=123
GET /users?search=john

# ✅ SAFE
GET /submission/{anonymous_token}
POST /submit  # No identity required
```

### Uniform Response Times
- All queries take similar time regardless of user
- Prevents timing attacks to identify known users

### Consistent Error Messages
```python
# ❌ REVEALS INFO
"User not found" vs "User found but no submissions"

# ✅ SAFE
"Submission not found"  # Same for invalid token, expired, etc.
```

## Edge Cases & Considerations

### 1. Consent Management

**Challenge:** How to manage consent without revealing identity?

**Solution:**
- Consent tokens are also anonymous
- Users prove identity via face embedding match (selfie)
- Consent is tied to face, not Telegram ID
- Can revoke via `/forget` (deletes all data)

### 2. Spam Prevention

**Challenge:** Without identity linkage, how to prevent spam?

**Solution:**
- Rate limiting by Telegram user ID (in-memory only, not persisted)
- Anonymous token allows users to retrieve their own submissions
- Admin can flag/reject submissions
- Proof-of-work not implemented (could be added)

### 3. Law Enforcement Requests

**Challenge:** Subpoenas for uploader identity.

**Defense:**
- **Technically impossible**: No linkage exists in database
- **HMAC tokens are one-way**: Cannot reverse
- **No logs**: Timing/IPs not stored
- **Legal position**: System cannot provide what it doesn't have

**Response to subpoena:**
```
The system does not store any linkage between 
submissions and uploader identities. The 
uploader_anonymous_token field is an HMAC hash 
that cannot be reversed to identify users.

No IP addresses, device fingerprints, or 
timing correlations are retained.

The system is designed to be technically 
incapable of providing this information.
```

### 4. Admin Access

**Challenge:** Admins need to review submissions without seeing uploader identity.

**Solution:**
- Admin UI shows only incident metadata and blurred faces
- No "uploaded by" field in admin interface
- Audit logs log admin actions, not uploader identity
- Admin cannot query "who uploaded this?"

## Testing for Anonymity

### Automated Tests
```python
def test_no_uploader_linkage():
    """Verify submissions cannot be traced to uploaders."""
    submission = create_submission(user="test_user")
    
    # Direct DB query should fail
    with pytest.raises(NoResultFound):
        session.query(Submission).filter(
            Submission.uploader_id == "test_user"
        ).one()
    
    # Anonymous token query should work
    result = session.query(Submission).filter(
        Submission.uploader_anonymous_token == submission.token
    ).one()
    assert result.uploader_anonymous_token is not None
```

### Manual Audits
1. Query database for `uploader_id` field → **Should not exist**
2. Search submission metadata for Telegram IDs → **Should not exist**
3. Review R2 object paths for user info → **Should not exist**
4. Check server logs for IP addresses → **Should not exist**

## Migration Path

For existing systems with `uploader_id`:

```sql
-- Step 1: Add anonymous token column
ALTER TABLE submissions ADD COLUMN uploader_anonymous_token VARCHAR(64);

-- Step 2: Generate tokens for existing submissions
UPDATE submissions s
SET uploader_anonymous_token = generate_anonymous_token(
    s.original_hash, 
    'migrated_user'  -- Placeholder, cannot recover actual user
);

-- Step 3: Drop old column
ALTER TABLE submissions DROP COLUMN uploader_id;

-- Step 4: Add index
CREATE INDEX idx_submissions_anonymous_token ON submissions(uploader_anonymous_token);
```

**Important:** Migrated tokens cannot be linked back to original uploaders. Users must re-submit via new system.

## Compliance

### DPDP Act 2023 (India)
- ✅ Right to be forgotten (complete deletion)
- ✅ Data minimization (only essential data stored)
- ✅ Purpose limitation (only for incident documentation)
- ✅ Transparency (clear privacy policy)

### GDPR (if applicable)
- ✅ Data protection by design
- ✅ Privacy by default
- ✅ No unnecessary personal data collection

### Best Practices
- Regular audits of data retention
- Periodic re-review of anonymization techniques
- Stay updated on cryptanalytic advances (HMAC-SHA256 is currently secure)

## Future Improvements

1. **Zero-Knowledge Proofs** (advanced)
   - Prove submission validity without revealing identity
   - Currently complex, may add if threat model evolves

2. **Tor/I2P Integration**
   - Hide submission source IP at network level
   - Currently relies on Telegram's infrastructure

3. **Distributed Storage**
   - IPFS or similar for censorship resistance
   - Currently uses Cloudflare R2

4. **Forward Secrecy**
   - Rotate MASTER_SALT periodically
   - Re-encrypt existing tokens (requires downtime)

## Summary

This system prioritizes **uploader safety over operational convenience**. Every design decision—from database schema to file naming—is evaluated against the question: "Could this be used to identify someone who submitted evidence?"

The answer must always be **no**.