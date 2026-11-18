# GTA 6 Content Tracker

Every 10 minutes this checks for new content and sends your phone a ranked list of the hottest **new** GTA 6:
🔴 live streams · 📱 Shorts · 🎬 videos · 🎙️ podcasts · 📼 stream VODs · ✂️ Twitch clips · 💬 Reddit posts

Items are ranked by **views per hour** (how fast they're blowing up), not total views, so you catch things early.
You only get each item once, including live streams. You're alerted when a stream starts, not every 10 minutes after that.
If nothing new turns up, you get no message.

Everything is free. No PC needs to stay on, because it runs on GitHub's servers.

### How often each source is checked
| Source | How often | Why |
|---|---|---|
| Twitch live streams and clips | every 10 min | free with no practical limit |
| Your watched YouTube channels (`watch_channels`) | every 10 min | uses the free RSS feed, so no quota |
| YouTube keyword search and live search | every 2 hours | each search costs 100 of your 10,000 daily quota units |

**Tip:** Add your 20–30 favourite GTA creators to `watch_channels` so you hear about their new uploads within about 10 minutes.

### ⚠️ Make the repository PUBLIC
GitHub gives **public** repositories unlimited free run minutes. A private one gets only 2,000 minutes a month,
and running every 10 minutes needs about 4,300, so a private repository would stop working partway through the month.
Your secrets (API keys and tokens) **stay hidden** in a public repository. Only the code and the list of seen videos are visible.
If you want to keep it private, change the schedule to `*/30 * * * *` (every 30 minutes).

---

## Setup (about 20 minutes, easiest on a computer)

### 1. Get a YouTube API key (free)
1. Go to https://console.cloud.google.com, sign in, and click **Create project**.
2. Search for **"YouTube Data API v3"** → **Enable**.
3. Go to **Credentials** → **Create credentials** → **API key**. Copy it.

### 2. Get Twitch keys (free, optional but recommended for clips)
1. Go to https://dev.twitch.tv/console and sign in. You need 2-factor authentication turned on.
2. **Register Your Application**: any name; OAuth Redirect URL `http://localhost`; category "Other".
3. Copy the **Client ID**, then click **New Secret** and copy the **Client Secret**.

### 3. Get the alerts on your phone (pick one or both)
**Telegram (recommended)**
1. In Telegram, message **@BotFather** → `/newbot` → follow the steps → copy the **bot token**.
2. Send any message to your new bot.
3. Open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and copy the number after `"chat":{"id":`. That's your **chat ID**.

**Discord**
- In your server: Channel settings → **Integrations** → **Webhooks** → **New Webhook** → **Copy URL**.

### 4. Put it on GitHub
1. Create a free account at https://github.com and make a **New repository**. Set it to **Public** (see the warning above).
2. Click **uploading an existing file** and drag in *all* files from this folder, including the `.github` folder.
   (If the `.github` folder won't upload, create the file manually: **Add file → Create new file**, name it
   `.github/workflows/tracker.yml`, and paste the contents in.)
3. In the repo go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Name | Value |
|---|---|
| `YOUTUBE_API_KEY` | from step 1 |
| `TWITCH_CLIENT_ID` | from step 2 |
| `TWITCH_CLIENT_SECRET` | from step 2 |
| `TELEGRAM_BOT_TOKEN` | from step 3 |
| `TELEGRAM_CHAT_ID` | from step 3 |
| `DISCORD_WEBHOOK_URL` | from step 3 (optional) |

### 5. Test it
**Actions** tab → **GTA 6 Tracker** → **Run workflow**. Within about a minute you should get your first digest.
After that it runs by itself every 10 minutes. When GitHub is busy, runs can be a few minutes late.

---

## Customise (edit `config.json` right on GitHub)

| Setting | What it does |
|---|---|
| `search_queries` | YouTube searches. Add things like `"GTA 6 Lucia"`, `"GTA 6 map"`, `"GTA 6 funny"` |
| `watch_channels` | Creators you always want to see, by @handle, e.g. `"@RockstarGames", "@SomeCreator"`. Marked ⭐ |
| `twitch_categories` | Twitch category names |
| `top_n` | How many items per section |
| `min_views` | Hide small videos (raise it to 10000 after launch, when there's a flood) |
| `lookback_hours` | How far back to look |
| `exclude_keywords` | Filter out junk, e.g. scam "free download" videos |
| `relevance_language` / `region_code` | e.g. `"en"` / `"US"` to focus results |

| `youtube_search_every_minutes` | How often the costly YouTube keyword search runs (default 120) |
| `repeat_live` | `true` lists live streams in every message while they're live |

To change how often it runs, edit the `cron` line in `.github/workflows/tracker.yml`.
`*/10 * * * *` = every 10 min; `*/30 * * * *` = every 30 min.

**YouTube quota:** the free limit is 10,000 units a day. Each run of keyword searches costs about 600
(5 searches + 1 live search). Every 2 hours that's about 7,200 a day, which is safe. Don't set
`youtube_search_every_minutes` below 90 unless you remove some searches.

## Run it on your own computer instead (optional)
```
pip install requests
# set the same keys as environment variables, then:
python tracker.py
```

## Notes
- Reddit often blocks requests from cloud servers. If you see a Reddit warning in the logs, that's
  normal and everything else still works.
- Remember the editing rules: use short clips, add your own commentary and changes, credit the creator,
  and don't use copyrighted music.
