#!/usr/bin/env python3
"""
GTA 6 Content Tracker
---------------------
Finds the hottest new GTA 6 videos, Shorts, live streams, podcasts and Twitch clips,
ranks them by how fast they're growing, and sends you a digest on Telegram and/or Discord.

Sources:
  - YouTube Data API v3 (keyword search + live streams + channels you watch)
  - Twitch Helix API (live streams + top clips in the GTA 6 category)
  - Reddit r/GTA6 (top posts, best-effort)

Secrets come from environment variables (see README.md).
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import xml.etree.ElementTree as ET

import requests

ROOT = Path(__file__).parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
SEEN_FILE = ROOT / "seen.json"
STATE_FILE = ROOT / "state.json"
try:
    STATE = json.loads(STATE_FILE.read_text())
except Exception:
    STATE = {}
REPORT_FILE = ROOT / "latest_report.md"

YT_KEY = os.getenv("YOUTUBE_API_KEY", "")
TW_ID = os.getenv("TWITCH_CLIENT_ID", "")
TW_SECRET = os.getenv("TWITCH_CLIENT_SECRET", "")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
DISCORD_HOOK = os.getenv("DISCORD_WEBHOOK_URL", "")

NOW = datetime.now(timezone.utc)
SINCE = NOW - timedelta(hours=CONFIG.get("lookback_hours", 24))
HTTP = requests.Session()
HTTP.headers["User-Agent"] = "gta6-tracker/1.0"


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def hours_since(iso_ts):
    ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    return max((NOW - ts).total_seconds() / 3600, 0.25)


def iso_duration_to_seconds(d):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return 0
    days, h, mi, s = (int(x or 0) for x in m.groups())
    return days * 86400 + h * 3600 + mi * 60 + s


def fmt_num(n):
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def fmt_dur(sec):
    if not sec:
        return ""
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ----------------------------------------------------------------------------- YouTube

YT = "https://www.googleapis.com/youtube/v3"


def yt_get(endpoint, **params):
    params["key"] = YT_KEY
    r = HTTP.get(f"{YT}/{endpoint}", params=params, timeout=30)
    if r.status_code != 200:
        log(f"YouTube {endpoint} error {r.status_code}: {r.text[:200]}")
        return {}
    return r.json()


def yt_search(query, live=False):
    params = dict(
        part="id",
        q=query,
        type="video",
        order="viewCount",
        maxResults=50,
        publishedAfter=SINCE.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    if live:
        params.pop("publishedAfter")
        params["eventType"] = "live"
    if CONFIG.get("relevance_language"):
        params["relevanceLanguage"] = CONFIG["relevance_language"]
    if CONFIG.get("region_code"):
        params["regionCode"] = CONFIG["region_code"]
    data = yt_get("search", **params)
    return [it["id"]["videoId"] for it in data.get("items", []) if it.get("id", {}).get("videoId")]


def yt_channel_id(handle):
    """Resolve @handle -> channel ID once, then remember it (saves quota)."""
    cache = STATE.setdefault("channel_ids", {})
    if handle in cache:
        return cache[handle]
    data = yt_get("channels", part="id", forHandle=handle.lstrip("@"))
    items = data.get("items", [])
    if not items:
        log(f"Channel not found: {handle}")
        return None
    cache[handle] = items[0]["id"]
    return cache[handle]


def yt_channel_uploads(handle):
    """Recent uploads of a watched channel via its free RSS feed (costs 0 quota)."""
    cid = yt_channel_id(handle)
    if not cid:
        return []
    try:
        r = HTTP.get("https://www.youtube.com/feeds/videos.xml", params={"channel_id": cid}, timeout=30)
        r.raise_for_status()
        ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
        root = ET.fromstring(r.content)
        ids = []
        for e in root.findall("a:entry", ns):
            vid = e.findtext("yt:videoId", namespaces=ns)
            pub = e.findtext("a:published", namespaces=ns)
            if vid and pub and hours_since(pub) <= CONFIG.get("lookback_hours", 24):
                ids.append(vid)
        return ids
    except Exception as ex:
        log(f"RSS failed for {handle}: {ex}")
        return []


def yt_details(ids):
    out = []
    ids = list(dict.fromkeys(ids))
    for i in range(0, len(ids), 50):
        data = yt_get("videos", part="snippet,statistics,contentDetails,liveStreamingDetails", id=",".join(ids[i:i + 50]))
        out.extend(data.get("items", []))
    return out


def classify_youtube(v):
    sn = v["snippet"]
    title = sn["title"]
    text = (title + " " + sn.get("description", "")[:300]).lower()
    secs = iso_duration_to_seconds(v.get("contentDetails", {}).get("duration"))
    if sn.get("liveBroadcastContent") == "live":
        return "live", secs
    if any(k in text for k in CONFIG.get("podcast_keywords", [])):
        return "podcast", secs
    if 0 < secs <= CONFIG.get("short_max_seconds", 180):
        return "short", secs
    if secs >= 3600 or v.get("liveStreamingDetails"):
        return "stream_vod", secs
    return "video", secs


def is_relevant(title, desc=""):
    text = (title + " " + desc[:500]).lower()
    must = [k.lower() for k in CONFIG.get("must_contain_any", [])]
    block = [k.lower() for k in CONFIG.get("exclude_keywords", [])]
    if must and not any(k in text for k in must):
        return False
    return not any(k in text for k in block)


def collect_youtube():
    if not YT_KEY:
        log("YOUTUBE_API_KEY missing - skipping YouTube")
        return []
    ids = []
    # Keyword searches are expensive (100 quota each), so they only run every N minutes.
    # Watched channels (RSS) and Twitch are checked on every run.
    every = CONFIG.get("youtube_search_every_minutes", 120)
    last = STATE.get("last_yt_search", 0)
    if (NOW.timestamp() - last) / 60 >= every - 2:
        for q in CONFIG.get("search_queries", []):
            found = yt_search(q)
            log(f"YouTube search '{q}': {len(found)}")
            ids += found
        live = yt_search(CONFIG.get("live_query", "GTA 6"), live=True)
        log(f"YouTube live now: {len(live)}")
        ids += live
        STATE["last_yt_search"] = NOW.timestamp()
    else:
        mins = every - (NOW.timestamp() - last) / 60
        log(f"YouTube keyword search skipped (next in ~{mins:.0f} min) - checking watched channels only")
    for handle in CONFIG.get("watch_channels", []):
        ids += yt_channel_uploads(handle)
    # re-check stats of videos already on the page (very cheap) so numbers stay fresh
    ids += [k[3:] for k in STATE.get("items", {}) if k.startswith("yt:")]

    items = []
    for v in yt_details(ids):
        sn, st = v["snippet"], v.get("statistics", {})
        if not is_relevant(sn["title"], sn.get("description", "")):
            continue
        kind, secs = classify_youtube(v)
        views = int(st.get("viewCount", 0))
        if kind == "live":
            viewers = int(v.get("liveStreamingDetails", {}).get("concurrentViewers", 0) or 0)
            score, metric = viewers, f"{fmt_num(viewers)} watching"
        else:
            if views < CONFIG.get("min_views", 0):
                continue
            hrs = hours_since(sn["publishedAt"])
            vph = views / hrs
            likes = int(st.get("likeCount", 0))
            comments = int(st.get("commentCount", 0))
            engagement = (likes + 3 * comments) / max(views, 1)
            score = vph * (1 + min(engagement * 10, 1))
            metric = f"{fmt_num(views)} views · {fmt_num(vph)}/hr · {hrs:.0f}h ago"
        items.append(dict(
            id=f"yt:{v['id']}", platform="YouTube", kind=kind, title=sn["title"],
            creator=sn["channelTitle"], url=f"https://youtu.be/{v['id']}",
            duration=fmt_dur(secs), score=score, metric=metric, published=sn["publishedAt"],
            thumb=(sn.get("thumbnails", {}).get("medium") or sn.get("thumbnails", {}).get("default") or {}).get("url", ""),
            watched=any(sn["channelTitle"].lower().replace(" ", "") in h.lower().replace(" ", "")
                        for h in CONFIG.get("watch_channels", [])),
        ))
    return items


# ----------------------------------------------------------------------------- Twitch

def twitch_token():
    r = HTTP.post("https://id.twitch.tv/oauth2/token", params=dict(
        client_id=TW_ID, client_secret=TW_SECRET, grant_type="client_credentials"), timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def collect_twitch():
    if not (TW_ID and TW_SECRET):
        log("Twitch keys missing - skipping Twitch")
        return []
    try:
        headers = {"Client-ID": TW_ID, "Authorization": f"Bearer {twitch_token()}"}
    except Exception as e:
        log(f"Twitch auth failed: {e}")
        return []

    def tget(path, **params):
        r = HTTP.get(f"https://api.twitch.tv/helix/{path}", headers=headers, params=params, timeout=30)
        if r.status_code != 200:
            log(f"Twitch {path} error {r.status_code}: {r.text[:200]}")
            return {}
        return r.json()

    game_ids = []
    for name in CONFIG.get("twitch_categories", []):
        g = tget("games", name=name).get("data", [])
        if g:
            game_ids.append(g[0]["id"])
        else:
            log(f"Twitch category not found: {name}")
    if not game_ids:
        return []

    items = []
    for gid in game_ids:
        for s in tget("streams", game_id=gid, first=50).get("data", []):
            items.append(dict(
                id=f"tw-live:{s['user_login']}:{s['started_at']}", platform="Twitch", kind="live",
                title=s["title"], creator=s["user_name"], url=f"https://twitch.tv/{s['user_login']}",
                duration="", score=s["viewer_count"],
                metric=f"{fmt_num(s['viewer_count'])} watching", watched=False, published=s["started_at"],
                thumb=s.get("thumbnail_url", "").replace("{width}", "440").replace("{height}", "248"),
            ))
        clips = tget("clips", game_id=gid, first=50,
                     started_at=SINCE.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     ended_at=NOW.strftime("%Y-%m-%dT%H:%M:%SZ")).get("data", [])
        for c in clips:
            hrs = hours_since(c["created_at"])
            items.append(dict(
                id=f"tw-clip:{c['id']}", platform="Twitch", kind="clip", title=c["title"],
                creator=c["broadcaster_name"], url=c["url"], duration=fmt_dur(int(c.get("duration", 0))),
                score=c["view_count"] / hrs,
                metric=f"{fmt_num(c['view_count'])} views · {hrs:.0f}h ago", watched=False,
                published=c["created_at"], thumb=c.get("thumbnail_url", ""),
            ))
    log(f"Twitch: {len(items)} items")
    return items


# ----------------------------------------------------------------------------- Reddit

def collect_reddit():
    items = []
    for sub in CONFIG.get("reddit_subs", []):
        try:
            r = HTTP.get(f"https://www.reddit.com/r/{sub}/top.json", params={"t": "day", "limit": 25}, timeout=30)
            if r.status_code != 200:
                log(f"Reddit r/{sub} returned {r.status_code} (often blocked from cloud servers - fine to ignore)")
                continue
            for p in r.json()["data"]["children"]:
                d = p["data"]
                hrs = max((NOW.timestamp() - d["created_utc"]) / 3600, 0.25)
                items.append(dict(
                    id=f"rd:{d['id']}", platform="Reddit", kind="reddit", title=d["title"],
                    creator=f"r/{sub}", url=f"https://reddit.com{d['permalink']}", duration="",
                    score=d["score"] / hrs, metric=f"{fmt_num(d['score'])} upvotes · {fmt_num(d['num_comments'])} comments",
                    watched=False, thumb="",
                    published=datetime.fromtimestamp(d["created_utc"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                ))
        except Exception as e:
            log(f"Reddit r/{sub} failed: {e}")
    return items


# ----------------------------------------------------------------------------- Report

SECTIONS = [
    ("live", "🔴 LIVE NOW"),
    ("short", "📱 HOT SHORTS"),
    ("video", "🎬 HOT VIDEOS"),
    ("podcast", "🎙️ PODCASTS"),
    ("stream_vod", "📼 STREAM VODs"),
    ("clip", "✂️ TWITCH CLIPS"),
    ("reddit", "💬 REDDIT BUZZ"),
]


def load_seen():
    try:
        data = json.loads(SEEN_FILE.read_text())
    except Exception:
        return {}
    cutoff = (NOW - timedelta(days=7)).timestamp()
    return {k: v for k, v in data.items() if v > cutoff}


def build_report(items, seen):
    top_n = CONFIG.get("top_n", 10)
    only_new = CONFIG.get("only_new", True)
    stamp = NOW.astimezone(timezone(timedelta(minutes=CONFIG.get("utc_offset_minutes", 330))))
    lines = [f"🎮 *GTA 6 Tracker* — {stamp.strftime('%a %d %b, %I:%M %p')}", ""]
    picked = []
    for kind, label in SECTIONS:
        group = [i for i in items if i["kind"] == kind]
        if only_new and (kind != "live" or not CONFIG.get("repeat_live", False)):
            group = [i for i in group if i["id"] not in seen]
        group.sort(key=lambda i: i["score"], reverse=True)
        group = group[:top_n]
        if not group:
            continue
        lines.append(f"*{label}*")
        for n, it in enumerate(group, 1):
            star = "⭐ " if it.get("watched") else ""
            dur = f" [{it['duration']}]" if it["duration"] else ""
            title = it["title"].replace("*", "").replace("_", " ").replace("[", "(").replace("]", ")")[:90]
            lines.append(f"{n}. {star}{title}{dur}")
            lines.append(f"   {it['creator']} · {it['platform']} · {it['metric']}")
            lines.append(f"   {it['url']}")
        lines.append("")
        picked += group
    if not picked:
        lines.append("Nothing new since the last check.")
    return "\n".join(lines), picked


def chunks(text, limit):
    buf = ""
    for block in text.split("\n\n"):
        piece = block + "\n\n"
        while len(piece) > limit:
            if buf:
                yield buf
                buf = ""
            yield piece[:limit]
            piece = piece[limit:]
        if len(buf) + len(piece) > limit:
            yield buf
            buf = ""
        buf += piece
    if buf.strip():
        yield buf


def send_telegram(text):
    if not (TG_TOKEN and TG_CHAT):
        return
    for part in chunks(text, 3900):
        r = HTTP.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", json=dict(
            chat_id=TG_CHAT, text=part, parse_mode="Markdown", disable_web_page_preview=True), timeout=30)
        if r.status_code != 200:  # retry without formatting if Markdown parsing failed
            HTTP.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", json=dict(
                chat_id=TG_CHAT, text=part.replace("*", ""), disable_web_page_preview=True), timeout=30)
        time.sleep(1)
    log("Sent to Telegram")


def send_discord(text):
    if not DISCORD_HOOK:
        return
    text = text.replace("*", "**")
    for part in chunks(text, 1900):
        # wrap links in <> so Discord doesn't create a huge preview for each one
        part = re.sub(r"(https?://\S+)", r"<\1>", part)
        HTTP.post(DISCORD_HOOK, json={"content": part}, timeout=30)
        time.sleep(1)
    log("Sent to Discord")


# ----------------------------------------------------------------------------- Web page

PAGE_DIR = ROOT / "docs"
TEMPLATE_FILE = ROOT / "page_template.html"


def update_store(items):
    """Keep everything from the last `lookback_hours` so the page stays full between searches."""
    store = STATE.setdefault("items", {})
    now = NOW.timestamp()
    for it in items:
        prev = store.get(it["id"])
        it["first_seen"] = prev["first_seen"] if prev else now
        it["last_seen"] = now
        store[it["id"]] = it
    live_ttl = CONFIG.get("live_expire_minutes", 25) * 60
    for k, it in list(store.items()):
        if it["kind"] == "live":
            if now - it.get("last_seen", 0) > live_ttl:
                del store[k]
        elif not it.get("published") or hours_since(it["published"]) > CONFIG.get("lookback_hours", 24):
            del store[k]
    # keep the page fast: max N per section
    cap = CONFIG.get("page_max_per_section", 60)
    for kind, _ in SECTIONS:
        group = sorted((i for i in store.values() if i["kind"] == kind), key=lambda i: i["score"], reverse=True)
        for it in group[cap:]:
            del store[it["id"]]
    return store


def write_page(store):
    PAGE_DIR.mkdir(exist_ok=True)
    data = dict(
        generated=NOW.timestamp(),
        every=CONFIG.get("page_refresh_minutes", 10),
        sections=[dict(kind=k, label=l) for k, l in SECTIONS],
        items=sorted(store.values(), key=lambda i: i["score"], reverse=True),
    )
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE_FILE.read_text(encoding="utf-8").replace("__DATA__", blob)
    (PAGE_DIR / "index.html").write_text(html, encoding="utf-8")
    (PAGE_DIR / ".nojekyll").write_text("")
    log(f"Web page written with {len(store)} items")


def main():
    items = collect_youtube() + collect_twitch()
    if CONFIG.get("reddit_subs"):
        items += collect_reddit()
    log(f"Total candidates: {len(items)}")

    write_page(update_store([dict(i) for i in items]))

    seen = load_seen()
    report, picked = build_report(items, seen)
    REPORT_FILE.write_text(report, encoding="utf-8")
    print("\n" + report)

    if picked or CONFIG.get("send_when_empty", False):
        send_telegram(report)
        send_discord(report)

    for it in picked:
        seen[it["id"]] = NOW.timestamp()
    SEEN_FILE.write_text(json.dumps(seen, indent=0))
    STATE_FILE.write_text(json.dumps(STATE, indent=1))


if __name__ == "__main__":
    sys.exit(main())
