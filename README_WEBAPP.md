# Telegram Mini App - Evidence Recorder

## Quick Start

The `/record` command opens a Telegram Mini App (WebApp) that runs entirely in the browser for maximum privacy.

## Architecture

```
┌─────────────┐      ┌──────────────┐      ┌─────────────┐
│   Telegram  │      │   Browser    │      │   Server    │
│   Bot       │─────>│   (WebApp)   │─────>│   (Python)  │
│             │      │              │      │             │
│ /record     │      │ face-api.js  │      │ Verify only │
│ button      │      │ blur faces   │      │ Store final │
│             │      │ strip EXIF   │      │ No originals│
└─────────────┘      └──────────────┘      └─────────────┘
```

## Privacy Guarantees

1. **Face detection runs in browser** - face-api.js, never sent to server
2. **Bystander faces blurred BEFORE upload** - server only sees blurred versions
3. **No originals bucket** - server never stores unblurred footage
4. **EXIF stripped client-side** - no GPS/metadata leakage
5. **No identity linkage** - anonymous token only for rate-limiting
6. **No admin content review** - automated checks only

## Files

```
webapp/
├── index.html             # Main upload page
├── face_detection.js      # face-api.js logic
└── manifest.json          # Telegram config

handlers/
├── webapp.py              # /record command
└── receive.py             # Handle WebApp data

processing/
└── verify.py              # Verification only (no blur)

migrations/
└── 003_miniapp_schema.sql # Mini App tables
```

## Usage

### For Users
1. Send `/record` to bot
2. Tap "Open Evidence Recorder"
3. Select photo/video
4. Tag detected faces:
   - 👤 Bystander → blurred (default)
   - 👮 Official → kept unblurred
5. Add incident details
6. Submit

### For Developers
```bash
# 1. Set WebApp URL in handlers/webapp.py
WEBAPP_URL = "https://your-domain.com/webapp/"

# 2. Host webapp/ directory on HTTPS server
# Telegram requires HTTPS for WebApps

# 3. Register /record command in bot.py
from handlers.webapp import get_webapp_handlers
application.add_handler(get_webapp_handlers())

# 4. Start bot
python bot.py
```

## Differences from Original System

| Feature | Original (Server-side) | Mini App (Client-side) |
|---------|----------------------|------------------------|
| Face detection | MediaPipe (server) | face-api.js (browser) |
| Blur | Server-side | Client-side |
| Originals | Stored in R2 | Never stored |
| Admin review | Manual approval | Automated only |
| Consent | Self/third-party | None needed |
| Identity | Anonymous token | Rate-limit token |
| Database | 8 tables | 1 table |

## Deployment

### Frontend (webapp/)
```bash
# Option 1: Static hosting (Netlify, Vercel, Cloudflare Pages)
# Just upload webapp/ directory

# Option 2: Same server as bot
# Serve webapp/ from Flask/FastAPI/nginx
```

### Bot
```bash
# Already configured in bot.py
# Just set WEBAPP_URL and deploy
```

## What's NOT Included (Out of Scope)

- ❌ Admin approve/reject content
- ❌ DM notifications to bystanders
- ❌ Server-side face blur
- ❌ Biometric database
- ❌ Originals storage

## Testing

```bash
# Test WebApp locally
cd webapp
python -m http.server 8080
# Open http://localhost:8080 in browser

# Test bot command
python bot.py
# Send /record in Telegram
```

## Security Notes

- All face detection is client-side only
- Server receives only blurred files
- No unblurred bystander footage ever touches server
- Hash verification for CSAM (integrate with provider)
- Rate-limiting prevents spam without identity tracking

## Next Steps

1. Deploy webapp/ to HTTPS hosting
2. Set WEBAPP_URL in handlers/webapp.py
3. Integrate CSAM hash check (NCMEC/IWF)
4. Add video blur support (ffmpeg.wasm)
5. Set up static mirror for publications