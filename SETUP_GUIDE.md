# 🔮 Project Oracle — Complete Setup Guide

> **Zero-cost, headless Instagram Reel/Feed Factory.**
> Controlled via Telegram. Runs on GitHub Actions. Posts autonomously.

---

## 📐 Architecture Overview

```
You (Telegram)
     │  /post stoicism
     ▼
Telegram Bot API
     │  update JSON
     ▼
Cloudflare Worker (relay)  ──►  GitHub API (repository_dispatch)
                                       │
                                       ▼
                              GitHub Actions Runner
                                       │
                          ┌────────────┼────────────────┐
                          ▼            ▼                 ▼
                    Gemini API   Pollinations.ai    Pixabay API
                  (text/logic)   (AI image gen)    (CC0 audio)
                          │            │                 │
                          └────────────┴─────────────────┘
                                       │
                                   FFmpeg
                                (render .mp4)
                                       │
                                       ▼
                            Instagram Graph API
                               (publish post)
                                       │
                                       ▼
                           📲 Telegram notification
                            "✅ Posted! [link]"
```

---

## 🗂️ Project File Structure

```
project_oracle/
├── main.py                        # Orchestrator (entry point)
├── telegram_webhook_handler.py    # Processes Telegram updates in Actions
├── requirements.txt
├── config.json                    # Auto-created; local dev state store
├── core/
│   ├── __init__.py
│   ├── state_manager.py           # Quota tracking + GitHub Gist persistence
│   ├── intelligence.py            # Gemini 1.5 Flash content generation
│   ├── image_generator.py         # Pollinations.ai + HuggingFace fallback
│   ├── audio_fetcher.py           # Pixabay CC0 audio downloader
│   ├── video_renderer.py          # FFmpeg: Ken Burns + text + audio merge
│   ├── instagram_publisher.py     # Instagram Graph API publisher
│   └── telegram_bot.py            # Telegram C2 bot
├── assets/
│   ├── images/                    # Downloaded/generated images (auto-created)
│   ├── audio/                     # Downloaded audio tracks (auto-created)
│   └── output/                    # Rendered videos (auto-created)
└── .github/
    └── workflows/
        └── workflow.yml           # GitHub Actions pipeline
```

---

## 🔑 Step 1 — Obtain All API Keys

### 1A. Gemini API Key (Free Tier)
1. Go to **https://aistudio.google.com/app/apikey**
2. Sign in with Google → **Create API Key**
3. Copy the key (starts with `AIza...`)
4. **Free tier limits:** 1,500 requests/day, 15 requests/minute

### 1B. Instagram Graph API (Meta for Developers)
This is the most involved step. Follow carefully.

**Prerequisites:**
- Instagram **Business** or **Creator** account (not personal)
- Facebook Page linked to your Instagram account

**Steps:**
1. Go to **https://developers.facebook.com** → Create App → "Other" → "Business"
2. Add "Instagram Graph API" product to your app
3. Go to **Graph API Explorer** (https://developers.facebook.com/tools/explorer)
4. Select your App → Get User Access Token
5. Select permissions: `instagram_basic`, `instagram_content_publish`, `pages_show_list`
6. Click "Generate Access Token" → copy it
7. Convert to **Long-Lived Token** (valid 60 days):
   ```
   GET https://graph.facebook.com/v19.0/oauth/access_token
     ?grant_type=fb_exchange_token
     &client_id={APP_ID}
     &client_secret={APP_SECRET}
     &fb_exchange_token={SHORT_TOKEN}
   ```
8. Get your **IG User ID**:
   ```
   GET https://graph.facebook.com/v19.0/me/accounts
     ?access_token={LONG_TOKEN}
   ```
   Then: `GET https://graph.facebook.com/v19.0/{PAGE_ID}?fields=instagram_business_account&access_token={TOKEN}`

> ⚠️ **Token Refresh:** Long-lived tokens expire in 60 days. Set a calendar reminder to refresh, or implement auto-refresh (see Appendix A).

### 1C. Telegram Bot
1. Open Telegram → search `@BotFather`
2. Send `/newbot` → follow prompts → copy the **bot token** (`123456:ABC-DEF...`)
3. Get your **personal Chat ID**:
   - Send any message to your bot
   - Visit: `https://api.telegram.org/bot{TOKEN}/getUpdates`
   - Find `"chat": {"id": XXXXXXX}` — that's your chat ID

### 1D. GitHub Gist (State Persistence)
1. Go to **https://gist.github.com**
2. Create a new **secret Gist** with filename `oracle_state.json` and content `{}`
3. Copy the Gist ID from the URL: `gist.github.com/{username}/{GIST_ID}`
4. Your `GITHUB_TOKEN` is auto-provided by GitHub Actions (no setup needed)

### 1E. Pixabay API Key (Optional — for CC0 audio)
1. Register at **https://pixabay.com/accounts/register/**
2. Go to **https://pixabay.com/api/docs/** → copy your API key
3. Free tier: unlimited requests

### 1F. Hugging Face Token (Optional — image fallback)
1. Register at **https://huggingface.co**
2. Go to Settings → Access Tokens → New Token (read scope)

---

## 🔐 Step 2 — Configure GitHub Secrets

Go to your repo → **Settings → Secrets and variables → Actions → New repository secret**

| Secret Name          | Value                                  | Required? |
|---------------------|----------------------------------------|-----------|
| `GEMINI_API_KEY`    | AIza... (from Step 1A)                 | ✅ Yes    |
| `IG_ACCESS_TOKEN`   | Long-lived Meta token (Step 1B)        | ✅ Yes    |
| `IG_USER_ID`        | Numeric Instagram user ID (Step 1B)    | ✅ Yes    |
| `TELEGRAM_BOT_TOKEN`| 123456:ABC-DEF... (Step 1C)            | ✅ Yes    |
| `TELEGRAM_CHAT_ID`  | Your numeric chat ID (Step 1C)         | ✅ Yes    |
| `GIST_ID`           | Gist UUID from URL (Step 1D)           | ⚠️ Recommended |
| `PIXABAY_API_KEY`   | Pixabay API key (Step 1E)              | Optional  |
| `HF_TOKEN`          | HuggingFace token (Step 1F)            | Optional  |

> `GITHUB_TOKEN` is **automatically** available in Actions — don't add it manually.

---

## 📲 Step 3 — Set Up Telegram → GitHub Actions Bridge

Since Telegram webhooks need a public HTTPS endpoint and GitHub Actions doesn't expose one, we use a **Cloudflare Worker** as a relay (free tier: 100,000 req/day).

### Deploy the Cloudflare Worker

1. Go to **https://dash.cloudflare.com** → Workers → Create Worker
2. Paste this code:

```javascript
// Cloudflare Worker: Telegram → GitHub Actions relay
export default {
  async fetch(request, env) {
    if (request.method !== 'POST') {
      return new Response('OK', { status: 200 });
    }

    const update = await request.json();
    
    // Forward to GitHub repository_dispatch
    const ghResponse = await fetch(
      `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/dispatches`,
      {
        method: 'POST',
        headers: {
          'Authorization': `token ${env.GH_PAT}`,
          'Content-Type': 'application/json',
          'User-Agent': 'ProjectOracle/1.0',
        },
        body: JSON.stringify({
          event_type: 'telegram_command',
          client_payload: update,
        }),
      }
    );

    return new Response(
      JSON.stringify({ ok: true, gh_status: ghResponse.status }),
      { headers: { 'Content-Type': 'application/json' } }
    );
  }
};
```

3. Add **Worker environment variables** (Settings → Variables):
   - `GH_OWNER` = your GitHub username
   - `GH_REPO` = your repository name
   - `GH_PAT` = GitHub Personal Access Token with `repo` scope (create at github.com/settings/tokens)

4. Deploy and copy your Worker URL: `https://your-worker.your-subdomain.workers.dev`

### Register Telegram Webhook

Run this once (from your terminal or Postman):

```bash
curl -X POST "https://api.telegram.org/bot{YOUR_BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://your-worker.your-subdomain.workers.dev"}'
```

Expected response: `{"ok":true,"result":true,"description":"Webhook was set"}`

---

## 🏃 Step 4 — First Test Run

### Option A: Test via Telegram
1. Send `/help` to your bot → should get the command list
2. Send `/post stoicism` → bot replies "Added to queue"
3. Wait for the next CRON trigger (or manually dispatch from Actions tab)

### Option B: Manual dispatch from GitHub
1. Go to your repo → **Actions → 🔮 Project Oracle**
2. Click **"Run workflow"**
3. Set Mode = `single`, Topic = `stoicism`, Type = `reel`
4. Click "Run workflow" → watch the logs

### Option C: Local development
```bash
# Clone and set up
git clone https://github.com/your-username/project-oracle
cd project-oracle
pip install -r requirements.txt

# Create .env file
cat > .env << EOF
GEMINI_API_KEY=AIza...
IG_ACCESS_TOKEN=EAABs...
IG_USER_ID=17841...
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=987654321
PIXABAY_API_KEY=...
EOF

# Run single post
python main.py single "stoicism" reel

# Start Telegram polling (local dev)
python main.py webhook
```

---

## ♾️ Step 5 — Continuous Generation Logic

The "Continuous Generation" system works as follows:

### Quota Guard Flow
```
CRON fires (every 6 hours)
        │
        ▼
StateManager.has_quota()?
   ├── NO  → Log "quota exceeded" → Exit (wait for midnight reset)
   └── YES ↓
        ▼
StateManager.get_topic_queue()
        │
        ├── EMPTY → Log "queue empty" → Exit
        └── HAS ITEMS ↓
              │
              ▼
        For each item in queue:
              │
              ├── has_quota()? NO → Stop loop
              ├── was_recently_posted()? YES → Skip, remove from queue
              └── YES → Run pipeline → Record post → decrement_quota()
                         → sleep 30s → next item
```

### Free-Tier Ceiling Logic

Each post consumes:
- **3 Gemini calls** (content generation prompt = 1 call; each call counts once)
- **1 image** from Pollinations (unlimited — no counter needed)

With Gemini's 1,500 req/day free limit and ~3 calls per post:
- **Max posts per day: ~500** (in theory; Instagram limits are lower)
- **Instagram posting limit: ~25 posts/day** for Graph API

The `StateManager` tracks both and stops when either ceiling is hit.

### Midnight Auto-Reset
The state resets quota counters automatically when the date changes:
```python
# In StateManager._reset_quota_if_new_day()
today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
if self._state["quota"]["reset_date"] != today:
    # Reset counters → save state
```

---

## 📊 Free Tier Limits Summary

| Service          | Free Limit           | Our Usage        | Headroom |
|-----------------|---------------------|-----------------|----------|
| GitHub Actions  | 2,000 min/month      | ~5 min/post      | ~400 posts/month |
| Gemini Flash    | 1,500 req/day        | ~3 req/post      | ~500 posts/day |
| Pollinations.ai | Unlimited            | 1 image/post     | ♾️ Unlimited |
| GitHub Gist     | Unlimited            | 1 write/post     | ♾️ Unlimited |
| Cloudflare Workers | 100,000 req/day   | 1 req/command    | ♾️ Unlimited |
| Pixabay         | Unlimited            | 1 track/10 posts | ♾️ Unlimited |
| **Instagram**   | **25 posts/day**     | 1 per run        | ⚠️ This is your real limit |

> ✅ **Real bottleneck: Instagram's own 25 posts/day limit.** All other services have far more headroom.

---

## 🧠 Understanding the Viral Logic

### Hook-Body-CTA Structure
Every post follows this 3-part structure:

```
┌─────────────────────────────┐
│  HOOK (top 15% of frame)    │  ← Creates curiosity. 12 words max.
│  "Most people live on        │    Bold, accent color, all-caps.
│   autopilot. Here's why:"   │
├─────────────────────────────┤
│                             │
│  BODY (center of frame)     │  ← The value. 40 words max.
│  "Marcus Aurelius spent      │    Regular weight, white text.
│   years training his mind..." │
│                             │
├─────────────────────────────┤
│  CTA (bottom 15% of frame)  │  ← Drives action. Saves & shares.
│  ↓ Save this for later.     │    Bold, accent color.
└─────────────────────────────┘
```

### The Ken Burns Effect
```
Frame 1 (0s):  zoom = 1.00  (original size)
Frame 450 (15s): zoom = 1.02
Frame 900 (30s): zoom = 1.04  (4% larger than start)
```
This subtle motion prevents Instagram's algorithm from classifying the post as a "static image" and gives it Reel-level distribution.

### Hashtag Strategy
Gemini generates exactly **7 hashtags** per post:
- 5-6 **niche tags** (e.g., `#stoicism`, `#dailystoic`, `#marcusaurelius`)
  → Places you in front of your exact audience
- 1 **broad tag** (e.g., `#philosophy`, `#mindset`)
  → Catches the algorithm's general category feed

**Why not 30 hashtags?** Instagram's algorithm since 2022 deprioritises posts that "hashtag stuff." 7 targeted tags outperform 30 generic ones.

---

## 🔧 Appendix A — Auto-Refresh Instagram Token

Add this to your CRON workflow or run monthly:

```python
# refresh_token.py
import os, httpx

token = os.environ["IG_ACCESS_TOKEN"]
app_id = os.environ["FB_APP_ID"]
app_secret = os.environ["FB_APP_SECRET"]

r = httpx.get(
    "https://graph.facebook.com/v19.0/oauth/access_token",
    params={
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": token,
    }
)
new_token = r.json()["access_token"]
print(f"New token (expires in 60 days): {new_token[:30]}...")
# Update your GitHub secret programmatically or manually
```

---

## 🚨 Appendix B — Troubleshooting

| Problem | Likely Cause | Fix |
|--------|-------------|-----|
| `GEMINI_API_KEY` error | Key not set in secrets | Add secret to repo settings |
| Image is black/blank | Pollinations timeout | Increase timeout or use HF fallback |
| FFmpeg `zoompan` slow | CPU-intensive filter | Reduce VIDEO_DURATION in renderer.py |
| IG API `(#10)` error | Wrong permissions | Re-generate token with correct scopes |
| Telegram bot not responding | Webhook not set | Re-run the `setWebhook` curl command |
| Container stuck `IN_PROGRESS` | Large video file | Reduce bitrate in video_renderer.py |
| `quota_exceeded` too early | Multiple runs in one day | Check `config.json` reset_date |

---

## 📬 Telegram Command Reference

| Command | Effect |
|---------|--------|
| `/post stoicism` | Add "stoicism" Reel to queue |
| `/feed mindfulness` | Add "mindfulness" Feed post to queue |
| `/now productivity` | Post immediately (bypasses queue) |
| `/status` | Show quota usage, queue size, last post |
| `/queue` | List all pending topics |
| `/clear` | Empty the queue |
| `/help` | Show all commands |
