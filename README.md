# 💼 Growth Hirings Tracker

Automatically tracks job postings from Slack → Notion.

**Flow:** You paste a job URL in `#growth-openings` on Slack → this script picks it up, extracts details via Claude, and adds a row to your Notion database.

---

## Setup Guide

### 1. Create a Slack App & Bot

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**
2. Name it `Growth Tracker Bot`, select your **getcrux** workspace
3. Go to **OAuth & Permissions** → add these **Bot Token Scopes**:
   - `channels:history` (read messages from public channels)
   - `groups:history` (if the channel is private)
   - `reactions:write` (to add ✅ reactions on processed messages)
4. **Install to Workspace** → copy the **Bot User OAuth Token** (`xoxb-...`)
5. **Invite the bot** to `#growth-openings`:
   - In Slack, go to the channel → type `/invite @Growth Tracker Bot`

### 2. Create a Notion Integration

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations) → **New integration**
2. Name it `Growth Tracker`, select your workspace
3. Copy the **Internal Integration Secret** (`ntn_...`)
4. **Share the database** with the integration:
   - Open the 💼 Growth Hirings database in Notion
   - Click `•••` (top right) → **Connections** → **Connect to** → select `Growth Tracker`

### 3. Get your Anthropic API Key

1. Go to [console.anthropic.com](https://console.anthropic.com) → **API Keys** → create one
2. Copy the key (`sk-ant-...`)

### 4. Deploy to Railway

1. Push this folder to a **GitHub repo**
2. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub Repo**
3. Select your repo
4. Go to **Variables** and add:
   ```
   SLACK_BOT_TOKEN=xoxb-...
   ANTHROPIC_API_KEY=sk-ant-...
   NOTION_TOKEN=ntn_...
   ```
5. Railway will auto-detect the `Procfile` and start the worker

That's it! 🎉

---

## How It Works

1. **Every 10 minutes**, the script reads new messages from `#growth-openings`
2. Extracts URLs from each message
3. Fetches the job posting page
4. Sends content to **Claude Sonnet** to extract: company, role, type, location, compensation
5. Creates a row in the **Notion database**
6. Adds a ✅ reaction to the Slack message
7. Tracks the last processed timestamp to avoid duplicates

### LinkedIn Post Date Detection

If you paste a LinkedIn post URL (e.g., `linkedin.com/feed/update/urn:li:activity:123456`), the script automatically decodes the posting date from the activity ID and uses it as the **Job Listed Date**.

---

## Local Testing

```bash
pip install -r requirements.txt
export SLACK_BOT_TOKEN=xoxb-...
export ANTHROPIC_API_KEY=sk-ant-...
export NOTION_TOKEN=ntn_...

# Run once
python main.py

# Run continuously
python scheduler.py
```

---

## Alternative: Deploy to Render

1. Push to GitHub
2. Go to [render.com](https://render.com) → **New** → **Background Worker**
3. Connect your repo
4. Set **Build Command**: `pip install -r requirements.txt`
5. Set **Start Command**: `python scheduler.py`
6. Add environment variables
7. Deploy

---

## Cost Estimate

- **Railway**: Free tier gives 500 hours/month (plenty for this)
- **Claude API**: ~$0.01–0.03 per job posting (Sonnet is cheap)
- **Notion API**: Free
- **Slack API**: Free

Total: **~$1-3/month** depending on volume.
