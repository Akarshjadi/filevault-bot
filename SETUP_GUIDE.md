# FileVault Bot — Complete Setup Guide

## Prerequisites

You need accounts on these services (all free):
1. **GitHub** — https://github.com (to host the code)
2. **Railway** — https://railway.app (to host the bot)
3. **Supabase** — https://supabase.com (for the database)
4. **Telegram** — https://t.me/BotFather (to create the bot)

---

## Step 1: Create a GitHub Repository

1. Go to https://github.com/new
2. Repository name: `filevault-bot` (or anything you like)
3. Keep it **Public** or **Private** — either works
4. **DO NOT** check "Add a README" or ".gitignore" (we already have them)
5. Click **Create repository**

After creating, you'll see a page with commands. Copy the one that looks like:
```
git remote add origin https://github.com/YOUR_USERNAME/filevault-bot.git
git branch -M main
git push -u origin main
```

Then run those commands in your terminal (from the `/Users/akarsh/Desktop/VS` folder).

---

## Step 2: Create a Telegram Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Choose a name (e.g., `FileVault Bot`)
4. Choose a username (must end in `bot`, e.g., `filevault_bot`)
5. BotFather will give you a **token** — save it! It looks like:
   ```
   1234567890:ABCdefGHIjklmNOPqrstUVwxyz-1234567
   ```

---

## Step 3: Create a Supabase Database

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

## Step 4: Deploy to Railway

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
6. Add another secret:
   - Name: `RAILWAY_SERVICE_NAME`
   - Value: (your service name from Railway URL, e.g., `filevault-bot`)

Now every time you push to `main`, it auto-deploys!

---

## Step 5: Set Yourself as Admin

After the bot is running:

1. Open Telegram and find your bot
2. Send `/start`
3. The bot will register you in the database
4. You need to manually set yourself as admin in Supabase:

   a. Go to Supabase → **SQL Editor**
   b. Run this query (replace YOUR_TELEGRAM_ID with your numeric ID):
   ```sql
   UPDATE "User" SET role = 'admin', is_approved = TRUE
   WHERE telegram_id = 'YOUR_TELEGRAM_ID';
   ```
   c. To find your Telegram ID, send `/myid` to the bot (it will work even without approval)

5. Now you can use admin commands:
   - `/approve_user` — approve users
   - `/list_pending` — see pending users
   - `/set_admin` — make other users admin
   - `/audit_log` — view activity log

---

## Step 6: Verify Everything Works

1. Send `/start` to the bot — should show your user info
2. Send `/myid` — shows your Telegram ID
3. Send `/stats` — shows bot statistics
4. Send a file — should be logged (if not approved, it will warn you)
5. Try `/help` — shows all commands

---

## Quick Reference: Useful Commands

### For you (admin):
| Command | Description |
|---------|-------------|
| `/approve_user` | Approve a pending user |
| `/reject_user` | Reject a user |
| `/list_pending` | List users awaiting approval |
| `/list_users` | List all registered users |
| `/set_admin` | Promote a user to admin |
| `/audit_log` | View recent activity |
| `/broadcast` | Send message to all users |

### For all users:
| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/help` | Show help |
| `/myid` | Get your Telegram ID |
| `/stats` | Bot statistics |
| `/organize` | Organize files (requires approval) |

---

## Troubleshooting

**Bot not responding?**
- Check Railway logs: Railway dashboard → your project → **Deployments** → click latest → **View logs**
- Verify `BOT_TOKEN` is correct in Railway Variables

**Database errors?**
- Check `DATABASE_URL` format — must have `+asyncpg` in it
- Make sure Supabase project is active
- Check Railway logs for connection errors

**Deployment failing?**
- Check GitHub Actions: your repo → **Actions** tab
- Verify `RAILWAY_TOKEN` secret is set correctly
- Try manual deploy: `railway up` from your terminal

**Need to reset the database?**
- Go to Supabase → **SQL Editor** and run:
  ```sql
  DROP TABLE IF EXISTS "AuditLog", "ApprovalRequest", "File", "User" CASCADE;
  ```
- Restart the bot (redeploy on Railway)