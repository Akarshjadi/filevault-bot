# FileVault Bot — Complete Setup Guide

## Prerequisites

You need accounts on these services (all free):
1. **GitHub** — https://github.com (to host the code)
2. **Railway** — https://railway.app (to host the bot)
3. **Supabase** — https://supabase.com (for the database)
4. **Telegram** — https://t.me/BotFather (to create the bot)

---

## Step 1: Create a Telegram Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Choose a name (e.g., `FileVault Bot`)
4. Choose a username (must end in `bot`, e.g., `filevault_bot`)
5. BotFather will give you a **token** — save it!
   ```
   1234567890:ABCdefGHIjklmNOPqrstUVwxyz-1234567
   ```

---

## Step 2: Create a Supabase Database

1. Go to https://supabase.com and sign up/login
2. Click **New project**
3. Name: `filevault-bot`
4. Set a **strong database password** — save it!
5. Choose a region close to you
6. Click **Create new project** (takes ~2 minutes)

Once created:
1. Go to **Project Settings** → **Database**
2. Find **Connection string** → **URI**
3. Copy the URI — it looks like:
   ```
   postgresql://postgres:YOUR_PASSWORD@db.xxxxx.supabase.co:5432/postgres
   ```
4. **Change it to use `asyncpg`**:
   ```
   postgresql+asyncpg://postgres:YOUR_PASSWORD@db.xxxxx.supabase.co:5432/postgres
   ```

---

## Step 3: Deploy to Railway

1. Go to https://railway.app and sign up/login (use GitHub login)
2. Click **New Project** → **Deploy from GitHub repo**
3. Select your `filevault-bot` repository
4. Railway will auto-detect the Dockerfile and deploy

### Add Environment Variables on Railway:

In your Railway project dashboard:
1. Go to **Variables**
2. Add these:

| Variable | Value |
|----------|-------|
| `BOT_TOKEN` | The token from BotFather |
| `DATABASE_URL` | The Supabase connection string (with `+asyncpg`) |
| `LOG_LEVEL` | `INFO` |

3. Railway will automatically redeploy

### Set up the GitHub → Railway auto-deploy:

1. In Railway, go to your project
2. Click **Settings** → **Generate Railway Token**
3. Copy the token
4. Go to your GitHub repo → **Settings** → **Secrets and variables** → **Actions**
5. Add a new secret:
   - Name: `RAILWAY_TOKEN`
   - Value: (paste the token)

Now every time you push to `main`, it auto-deploys!

---

## Step 4: Set Yourself as Admin

After the bot is running:

1. Open Telegram and find your bot
2. Send `/start` to the bot
3. The bot will register you in the database as PENDING
4. You need to manually set yourself as admin in Supabase:

   a. Go to Supabase → **SQL Editor**
   b. Run this query (replace YOUR_TELEGRAM_ID with your numeric ID):
   ```sql
   UPDATE "User" SET role = 'admin', is_approved = TRUE
   WHERE telegram_id = 'YOUR_TELEGRAM_ID';
   ```
   c. To find your Telegram ID, send `/myid` to the bot (it will work even without approval)

5. Now you are an admin and can:
   - `/admin` — See admin dashboard
   - `/admin approve <user_id>` — Approve pending users
   - View and approve/reject file submissions

---

## Step 5: Set Up a Vault Group

1. Create a **supergroup** in Telegram
2. Add your bot to the group as **admin**
3. In the group, send `/setvault`
4. The bot will bind this group as the vault for your account

---

## Step 6: How It Works

### User Registration
- Anyone can send `/start` to the bot
- They are registered as **PENDING** — cannot use commands or save files
- Admin uses `/admin approve <user_id>` to approve them
- Approved users get **WHITELISTED** role

### File Submission Flow
1. User sends a file to the bot in DM
2. Bot forwards the file to **ALL admins** with ✅ Approve / ❌ Reject buttons
3. Admin reviews the file:
   - **Approve** → file is copied to the user's vault group, user is notified
   - **Reject** → user is notified, file is not stored
4. Only approved files appear in the user's vault

---

## Step 7: Verify Everything Works

1. Send `/start` to the bot — should show welcome message
2. Send `/myid` — shows your Telegram ID
3. Admin approves you via Supabase SQL or `/admin approve`
4. Set up a vault group with `/setvault`
5. Send a file — should go to admin for review
6. Admin approves — file appears in vault group

---

## Quick Reference: Admin Commands

| Command | Description |
|---------|-------------|
| `/admin` | Admin dashboard |
| `/admin users` | List all users |
| `/admin approve <id>` | Approve pending user |
| `/admin deny <id>` | Deny/block user |
| `/admin stats` | System statistics |
| `/admin logs` | View audit logs |
| `/admin broadcast <msg>` | Message all users |
| `/admin setrole <id> <role>` | Change user role |
| `/admin vault <id>` | View user's vault |
| `/setvault` | Bind current group as vault (run in group) |
| `/adduser <user_id>` | Add user to whitelist |
| `/removeuser <user_id>` | Block user |

---

## Troubleshooting

**Bot not responding?**
- Check Railway logs: Railway dashboard → your project → **Deployments** → click latest → **View logs**
- Verify `BOT_TOKEN` is correct in Railway Variables

**Database errors?**
- Check `DATABASE_URL` format — must have `+asyncpg` in it
- Make sure Supabase project is active
- Run migrations if needed: the bot auto-creates tables on startup

**Files not being sent to admin?**
- Make sure at least one admin user exists in the database with `role = 'admin'`
- Check Railway logs for errors

**Deployment failing?**
- Check GitHub Actions: your repo → **Actions** tab
- Verify `RAILWAY_TOKEN` secret is set correctly
- Try manual deploy from Railway dashboard

---

## Database Schema

The bot uses these tables (auto-created on startup):
- `users` — User accounts with roles and approval status
- `pending_files` — Files awaiting admin approval
- `files` — Approved files stored in vaults
- `vaults` — Vault group bindings
- `audit_logs` — Action history

## Security Notes

- All user-provided content is escaped to prevent Markdown injection
- Files are reviewed by admins before being stored
- User registration requires admin approval
- Audit logging tracks all actions