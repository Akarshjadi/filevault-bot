# Deployment Guide - FileVault Evidence Vault

Deploy to Railway (backend), Supabase (database), Cloudflare R2 (storage), and GitHub (code).

## Prerequisites

- GitHub account
- Railway CLI installed (`npm i -g @railway/cli`)
- Supabase account
- Cloudflare R2 account

## Step 1: GitHub

### Initialize and Push

```bash
# Initialize git (if not already done)
git init
git add .
git commit -m "Initial commit: FileVault Evidence Vault with Mini App"

# Create repo on GitHub: https://github.com/new
# Name: filevault-bot

# Add remote and push
git remote add origin https://github.com/YOUR_USERNAME/filevault-bot.git
git branch -M main
git push -u origin main
```

## Step 2: Supabase (Database)

### Create Project

1. Go to [supabase.com](https://supabase.com)
2. Click "New Project"
3. Name: `filevault`
4. Set database password (save it!)
5. Wait for provisioning (~2 minutes)

### Get Connection String

1. Go to Project Settings → Database
2. Find "Connection pooling" section
3. Copy the connection string (choose "Session pooler" for better performance):
   ```
   postgresql+asyncpg://postgres.[PROJECT_REF]:[PASSWORD]@aws-0-[REGION].pooler.supabase.com:5432/postgres
   ```

### Run Migrations

```bash
# Option A: Via bot startup (automatic)
# Just deploy to Railway - migrations run automatically

# Option B: Manual (via psql or Supabase SQL Editor)
# Copy migrations/001_dpdp_schema.sql, migrations/002_evidence_vault.sql, migrations/003_miniapp_schema.sql
# Paste into Supabase Dashboard → SQL Editor → Run
```

### Enable Extensions

In Supabase Dashboard → SQL Editor, run:

```sql
-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
```

## Step 3: Cloudflare R2 (Storage)

### Create Buckets

1. Go to [Cloudflare Dashboard](https://dash.cloudflare.com) → R2
2. Click "Create bucket"
3. Create these buckets:
   - `evidence-originals` (for original uploads - 90-day retention)
   - `evidence-exif` (for EXIF data - 1-year retention)
   - `evidence-processing` (for working copies - temporary)
   - `evidence-published` (for final blurred versions - long-term)

### Get Credentials

1. Go to R2 → Manage R2 API Tokens
2. Click "Create API Token"
3. Permissions: "Object Read & Write" for all buckets
4. Save:
   - Access Key ID
   - Secret Access Key
   - Endpoint URL (format: `https://[ACCOUNT_ID].r2.cloudflarestorage.com`)

## Step 4: Railway (Backend)

### Initialize Railway Project

```bash
# Install Railway CLI
npm i -g @railway/cli

# Login
railway login

# Initialize project in your repo directory
cd /path/to/filevault-bot
railway init

# Link to existing project or create new
railway link
```

### Configure Environment Variables

In Railway Dashboard → Variables, add:

```bash
# Required
BOT_TOKEN=<your_telegram_bot_token>
DATABASE_URL=<supabase_connection_string>
S3_ENDPOINT_URL=<cloudflare_r2_endpoint>
S3_ACCESS_KEY_ID=<r2_access_key>
S3_SECRET_ACCESS_KEY=<r2_secret_key>
ENCRYPTION_MASTER_SALT=<64_char_hex_salt>
ADMIN_IDS=<telegram_user_id_1>,<telegram_user_id_2>

# R2 Buckets (use defaults if not customized)
R2_BUCKET_ORIGINALS=evidence-originals
R2_BUCKET_EXIF=evidence-exif
R2_BUCKET_PROCESSING=evidence-processing
R2_BUCKET_PUBLISHED=evidence-published

# WebApp (when deployed)
WEBAPP_URL=https://your-domain.com/webapp/index.html

# Optional
LOG_LEVEL=INFO
STORAGE_BASE=./vault_storage
```

### Generate ENCRYPTION_MASTER_SALT

```bash
python -c "import secrets; print(secrets.token_hex(32))"
# Copy the 64-character hex string
```

### Deploy

```bash
# Deploy to Railway
railway up

# Or connect GitHub repo in Railway dashboard for auto-deploy
```

### Verify Deployment

```bash
# Check logs
railway logs

# Test health endpoint (if you add one)
curl https://your-app.up.railway.app/health
```

## Step 5: Telegram Bot Setup

### Create Bot

1. Open Telegram, search for [@BotFather](https://t.me/BotFather)
2. Send `/newbot`
3. Name: `FileVault Evidence Bot`
4. Username: `@filevault_[something]_bot`
5. Save the token

### Enable Mini App

1. Talk to [@BotFather](https://t.me/BotFather)
2. Send `/mybots`
3. Select your bot
4. Click "Bot Settings" → "Menu Button" → "Configure Menu Button"
5. Set menu button:
   - Text: "📸 Record Evidence"
   - URL: Leave empty for now (set after WebApp deployment)

### Set Commands

Send `/setcommands` to BotFather:

```
start - Start registration
help - Get help
about - About FileVault
record - Open Evidence Recorder Mini App
submit - Submit evidence (legacy)
my_data - View your profile
forget - Delete your data (right to be forgotten)
webapp_info - Learn about Mini App
```

## Step 6: Deploy WebApp (Mini App Frontend)

### Option A: Netlify (Recommended)

```bash
# Install Netlify CLI
npm i -g netlify-cli

# Deploy webapp directory
cd webapp
netlify deploy --prod --dir=.

# Set custom domain if needed
netlify domains:add your-domain.com
```

### Option B: Vercel

```bash
# Install Vercel CLI
npm i -g vercel

# Deploy
cd webapp
vercel --prod
```

### Option C: Same Railway Project

Add a second service in Railway:

1. In Railway dashboard, click "New"
2. Select "GitHub Repo"
3. Choose same repo
4. Set "Root Directory" to `webapp/`
5. Set "Start Command" to `python -m http.server $PORT`
6. Deploy

Then update `handlers/webapp.py`:
```python
WEBAPP_URL = "https://your-railway-app.up.railway.app"
```

## Step 7: Configure WebApp URL

After deploying the WebApp:

1. Update `handlers/webapp.py`:
   ```python
   WEBAPP_URL = "https://your-actual-domain.com/webapp/index.html"
   ```

2. Redeploy Railway:
   ```bash
   railway up
   ```

3. Update Telegram bot menu button:
   - Talk to @BotFather
   - `/mybots` → Select bot → "Bot Settings" → "Menu Button"
   - Set URL to: `https://t.me/your_bot_username?startapp=record`

## Step 8: Test Deployment

### Test Bot

```bash
# Send to your bot in Telegram:
/start
/record
# Should open WebApp button
```

### Test WebApp

1. Tap "/record" in Telegram
2. Should open WebApp
3. Upload test image
4. Verify face detection works
5. Verify blur works
6. Submit and check server receives it

### Test Database

```bash
# Via Supabase Dashboard → Table Editor
# Check that webapp_submissions table exists
# Verify submissions appear after WebApp use
```

### Test R2

```bash
# Via Cloudflare Dashboard → R2
# Check that files appear in evidence-published bucket
# Verify presigned URLs work
```

## Step 9: Production Checklist

- [ ] Telegram bot token configured
- [ ] Supabase database created and migrations run
- [ ] R2 buckets created
- [ ] Railway environment variables set
- [ ] WebApp deployed to HTTPS
- [ ] WEBAPP_URL updated in bot code
- [ ] Bot commands set in BotFather
- [ ] Test submission end-to-end
- [ ] Monitor logs for errors
- [ ] Set up alerts (optional)

## Troubleshooting

### Bot not starting
- Check `railway logs` for errors
- Verify DATABASE_URL format (must be asyncpg)
- Verify BOT_TOKEN is correct

### WebApp not loading
- Ensure HTTPS (Telegram requires it)
- Check browser console for errors
- Verify face-api.js CDN is accessible

### Database errors
- Run migrations manually via Supabase SQL Editor
- Check extensions are enabled (pgcrypto, uuid-ossp)

### R2 upload failures
- Verify S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY
- Check bucket names match
- Verify endpoint URL format

## Monitoring

```bash
# View Railway logs
railway logs --tail

# Monitor database in Supabase
# Dashboard → Logs → Postgres Logs

# Monitor R2 in Cloudflare
# Dashboard → Analytics
```

## Scaling

- Railway: Upgrade to Pro for more resources
- Supabase: Connection pooling already configured
- R2: No limits, pay per operation

## Support

- Railway: [docs.railway.app](https://docs.railway.app)
- Supabase: [supabase.com/docs](https://supabase.com/docs)
- Cloudflare R2: [developers.cloudflare.com/r2](https://developers.cloudflare.com/r2)