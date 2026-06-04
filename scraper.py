#!/usr/bin/env python3
"""
scraper.py

Extraction strategy
-------------------
ONLY use context-anchored matches: a token is a code only if it is
directly preceded by a label keyword (code / promo / bonus / etc.)
followed by a separator.  This eliminates follower-count false positives
like "91K", "71K", plain numbers, and random short words.

post_date is scraped alongside each code so the UI can show
"posted X ago" instead of "scraped X ago".
"""

import json
import re
import time
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

OUTPUT_FILE      = Path("codes.json")
MAX_HISTORY_DAYS = 30

GAMES = {
    "gaminator": {
        "facebook_url":   "https://www.facebook.com/gaminator3000",
        "instagram_user": "gaminator",
        "tiktok_user":    "gaminator3000",
        "taplink_url":    "https://taplink.cc/gaminator3000",
    },
    "slotpark": {
        "facebook_url":   "https://www.facebook.com/slotpark",
        "instagram_user": "slotpark",
        "tiktok_user":    "slotparkslots",
        "taplink_url":    None,
    },
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

# --------------------------------------------------------------------------
# EXTRACTION  –  context-only, no bare-token fallback
# --------------------------------------------------------------------------
# Matches:  CODE: nmv5   /  promo - AB12  /  bonus=XY9  /  redeem: k8n95
# Does NOT match:  "91K followers", "71K", plain numbers, plain words
# --------------------------------------------------------------------------
CODE_RE = re.compile(
    r'(?:free\s*code|promo\s*code|bonus\s*code|coupon\s*code'
    r'|redeem\s*code|gift\s*code|reward\s*code'
    r'|promo|coupon|redeem|gift|reward|code)'
    r'[\s:=\-\u2013\u2014\u25ba\u2192\u00bb]+'
    r'([A-Za-z0-9]{3,10})'
    r'(?=[\s,;.!\)\]\"\u2019]|$)',
    re.IGNORECASE | re.MULTILINE,
)

# Hard blacklist: these tokens are NEVER codes even after a label
BLACKLIST = {
    "HTTPS", "HTTP", "WWW", "COM", "NET", "ORG", "APP", "APK",
    "FACEBOOK", "INSTAGRAM", "TIKTOK", "TAPLINK",
    "GAMINATOR", "SLOTPARK",
    "BONUS", "PROMO", "CODE", "CODES",
    "FREE", "COINS", "CHIP", "CHIPS", "SPIN", "SPINS",
    "DAILY", "TODAY", "WEEK", "MONTH", "NEW", "GET", "WIN",
    "PLAY", "MORE", "JOIN", "LIKE", "SHARE", "FOLLOW", "CLICK",
    "DOWNLOAD", "INSTALL", "UPDATE", "LOGIN", "REGISTER",
    "CEST", "CET", "UTC", "GMT", "PM", "AM",
    "HERE", "LINK", "NOW", "BELOW", "ABOVE", "CLICK",
}


def extract_codes(text: str) -> list[str]:
    """Return sorted list of unique uppercase code tokens found in text."""
    if not text:
        return []
    found = set()
    for m in CODE_RE.finditer(text):
        token = m.group(1).upper()
        if token in BLACKLIST:
            continue
        if token.isdigit():
            continue
        # Reject pure-number with K/M suffix  (e.g. 91K, 71K, 1M)
        if re.fullmatch(r'\d+[KMBkmb]', token):
            continue
        if len(token) < 3:
            continue
        log.info("  [code found] %s  (context: %s)", token, m.group(0)[:40])
        found.add(token)
    return sorted(found)


# --------------------------------------------------------------------------
# DATE PARSING helpers
# --------------------------------------------------------------------------

REL_DATE_RE = re.compile(
    r'(\d+)\s*(second|minute|hour|day|week|month|year)s?\s*ago',
    re.IGNORECASE,
)
MONTH_MAP = {
    'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,
    'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12,
}


def parse_relative_date(text: str) -> str | None:
    """Try to parse a relative date string like '3 hours ago' to ISO."""
    m = REL_DATE_RE.search(text)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    delta_map = {
        'second': timedelta(seconds=n),
        'minute': timedelta(minutes=n),
        'hour':   timedelta(hours=n),
        'day':    timedelta(days=n),
        'week':   timedelta(weeks=n),
        'month':  timedelta(days=n * 30),
        'year':   timedelta(days=n * 365),
    }
    dt = datetime.now(timezone.utc) - delta_map.get(unit, timedelta(0))
    return dt.isoformat()


def parse_abs_date(text: str) -> str | None:
    """Try a few absolute date patterns (Facebook / TikTok style)."""
    # "May 15" or "May 15, 2025" or "15 May 2025"
    patterns = [
        r'(\w+)\s+(\d{1,2}),?\s+(\d{4})',   # May 15, 2025
        r'(\d{1,2})\s+(\w+)\s+(\d{4})',       # 15 May 2025
        r'(\w+)\s+(\d{1,2})(?!\s*,?\s*\d{4})', # May 15 (no year → current year)
    ]
    now = datetime.now(timezone.utc)
    for pat in patterns:
        m = re.search(pat, text)
        if not m:
            continue
        try:
            groups = m.groups()
            if len(groups) == 3:
                a, b, c = groups
                # figure out which is month name
                if a[:3].lower() in MONTH_MAP:
                    month, day, year = MONTH_MAP[a[:3].lower()], int(b), int(c)
                elif b[:3].lower() in MONTH_MAP:
                    day, month, year = int(a), MONTH_MAP[b[:3].lower()], int(c)
                else:
                    continue
            else:  # 2 groups: month + day, assume current year
                a, b = groups
                if a[:3].lower() in MONTH_MAP:
                    month, day, year = MONTH_MAP[a[:3].lower()], int(b), now.year
                else:
                    continue
            dt = datetime(year, month, day, 12, 0, tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            continue
    return None


def best_date(strings: list[str]) -> str | None:
    """Given a list of candidate date strings, return the best ISO date."""
    for s in strings:
        d = parse_relative_date(s)
        if d:
            return d
    for s in strings:
        d = parse_abs_date(s)
        if d:
            return d
    return None


# --------------------------------------------------------------------------
# ITEM BUILDER
# --------------------------------------------------------------------------

def now_utc(): return datetime.now(timezone.utc)
def iso_now(): return now_utc().isoformat()
def make_id(game, code): return hashlib.md5(f"{game}:{code}".encode()).hexdigest()[:12]


def build_item(game, code, platform, url="", post_date: str | None = None):
    return {
        "id":          make_id(game, code),
        "game":        game,
        "code":        code,
        "sources":     [platform],
        # post_date = when the post was published (best effort)
        # found_at  = when the scraper scraped it
        "post_date":   post_date or iso_now(),
        "found_at":    iso_now(),
        "raw_sources": [{"platform": platform, "url": url}],
    }


# --------------------------------------------------------------------------
# PERSISTENCE
# --------------------------------------------------------------------------

def load_existing():
    if not OUTPUT_FILE.exists(): return []
    try:
        data = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
        return data.get("codes", []) if isinstance(data, dict) else data
    except Exception as e:
        log.warning("Could not read codes.json: %s", e)
        return []


def save_codes(items):
    OUTPUT_FILE.write_text(
        json.dumps({"updated_at": iso_now(), "codes": items}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def merge_codes(existing, new_items):
    by_key = {f"{i['game']}::{i['code']}": i for i in existing}
    for item in new_items:
        key = f"{item['game']}::{item['code']}"
        if key not in by_key:
            by_key[key] = item
            continue
        cur = by_key[key]
        seen = {(s["platform"], s.get("url", "")) for s in cur.get("raw_sources", [])}
        for src in item.get("raw_sources", []):
            pair = (src["platform"], src.get("url", ""))
            if pair not in seen:
                cur.setdefault("raw_sources", []).append(src)
                seen.add(pair)
        cur["sources"] = sorted({s["platform"] for s in cur.get("raw_sources", [])})
        # keep earliest post_date we've seen
        if item.get("post_date") and item["post_date"] < cur.get("post_date", item["post_date"]):
            cur["post_date"] = item["post_date"]

    cutoff = now_utc() - timedelta(days=MAX_HISTORY_DAYS)
    merged = [v for v in by_key.values()
              if datetime.fromisoformat(v.get("found_at", iso_now())) >= cutoff]
    merged.sort(key=lambda x: x.get("post_date", x["found_at"]), reverse=True)
    return merged


# --------------------------------------------------------------------------
# SCRAPERS
# --------------------------------------------------------------------------

def pw_get_text(page, url: str, wait_ms: int = 4000) -> str:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(wait_ms)
        text = page.inner_text("body")
        log.info("  [%s] body sample: %s", url[:60], text[:400].replace("\n", " "))
        return text
    except Exception as e:
        log.warning("pw_get_text failed %s: %s", url, e)
        return ""


def scrape_taplink_pw(page, game, url):
    """Taplink bio page – no post date, use scrape time."""
    log.info("[%s] Taplink: %s", game, url)
    text = pw_get_text(page, url, wait_ms=5000)
    results = []
    for code in extract_codes(text):
        log.info("[%s] Taplink -> %s", game, code)
        # bio has no post date; found_at == post_date (updated live)
        results.append(build_item(game, code, "taplink", url, post_date=iso_now()))
    return results


def scrape_facebook_pw(page, game, url):
    """
    Use Playwright on mbasic Facebook to actually render the page.
    mbasic gives real HTML without requiring login for public pages.
    We look at each "story" block to extract code + timestamp together.
    """
    log.info("[%s] Facebook PW: %s", game, url)
    mbasic = url.replace("www.facebook.com", "mbasic.facebook.com")
    results = []
    try:
        page.goto(mbasic, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")

        # Each post on mbasic is wrapped in a <div> with id like "u_0_..."
        # The timestamp is usually in an <abbr> or a small <span>
        posts = soup.find_all("div", attrs={"data-ft": True})
        if not posts:
            # fallback: treat whole page as one block
            posts = [soup]

        for post in posts:
            block_text = post.get_text(" ", strip=True)
            codes = extract_codes(block_text)
            if not codes:
                continue

            # Try to find a date near this post
            date_candidates = []
            for abbr in post.find_all("abbr"):
                date_candidates.append(abbr.get("title", "") or abbr.get_text())
            for span in post.find_all("span"):
                t = span.get_text(strip=True)
                if re.search(r'(ago|\d{4}|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)', t, re.I):
                    date_candidates.append(t)
            post_date = best_date(date_candidates)

            for code in codes:
                log.info("[%s] Facebook -> %s  (post_date=%s)", game, code, post_date)
                results.append(build_item(game, code, "facebook", url, post_date=post_date))

    except Exception as e:
        log.warning("[%s] Facebook PW failed: %s", game, e)
    return results


def scrape_instagram_pw(page, game, username):
    """
    Instagram profile page via Playwright.
    We try to read the bio area and any visible caption text.
    Instagram requires login to see post timestamps; for the bio we use
    scrape time as post_date.
    """
    log.info("[%s] Instagram: @%s", game, username)
    url = f"https://www.instagram.com/{username}/"
    results = []
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        full_text = soup.get_text(" ", strip=True)

        codes = extract_codes(full_text)
        for code in codes:
            log.info("[%s] Instagram -> %s", game, code)
            # Instagram scrape: use now as post_date (bio-style)
            results.append(build_item(game, code, "instagram", url, post_date=iso_now()))
    except Exception as e:
        log.warning("[%s] Instagram failed: %s", game, e)
    return results


def scrape_tiktok_pw(page, game, username):
    """
    TikTok profile page via Playwright.
    Visible post captions sometimes include codes and relative timestamps.
    """
    log.info("[%s] TikTok: @%s", game, username)
    url = f"https://www.tiktok.com/@{username}"
    results = []
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(6000)  # TikTok is slow
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")

        # Try per-video containers for code + date pairing
        video_items = soup.find_all(attrs={"data-e2e": re.compile(r'user-post', re.I)})
        if not video_items:
            video_items = soup.find_all("div", class_=re.compile(r'DivItemContainer', re.I))
        if not video_items:
            # fallback: whole page
            text = soup.get_text(" ", strip=True)
            for code in extract_codes(text):
                log.info("[%s] TikTok (page) -> %s", game, code)
                results.append(build_item(game, code, "tiktok", url))
            return results

        for item in video_items:
            block_text = item.get_text(" ", strip=True)
            codes = extract_codes(block_text)
            if not codes:
                continue
            date_candidates = []
            for span in item.find_all(["span", "p", "time"]):
                t = span.get("datetime") or span.get_text(strip=True)
                if t and re.search(r'(ago|\d{4}|\d+-\d+-\d+)', t, re.I):
                    date_candidates.append(t)
            post_date = best_date(date_candidates)
            for code in codes:
                log.info("[%s] TikTok -> %s  (post_date=%s)", game, code, post_date)
                results.append(build_item(game, code, "tiktok", url, post_date=post_date))

    except Exception as e:
        log.warning("[%s] TikTok failed: %s", game, e)
    return results


# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------

def main():
    existing  = load_existing()
    new_items = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="en-US",
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.new_page()

        for game, cfg in GAMES.items():
            log.info("=== %s ===", game)

            if cfg.get("taplink_url"):
                try: new_items.extend(scrape_taplink_pw(page, game, cfg["taplink_url"]))
                except Exception as e: log.error("[%s] Taplink error: %s", game, e)
                time.sleep(2)

            try: new_items.extend(scrape_facebook_pw(page, game, cfg["facebook_url"]))
            except Exception as e: log.error("[%s] Facebook error: %s", game, e)
            time.sleep(2)

            try: new_items.extend(scrape_instagram_pw(page, game, cfg["instagram_user"]))
            except Exception as e: log.error("[%s] Instagram error: %s", game, e)
            time.sleep(2)

            try: new_items.extend(scrape_tiktok_pw(page, game, cfg["tiktok_user"]))
            except Exception as e: log.error("[%s] TikTok error: %s", game, e)
            time.sleep(2)

        browser.close()

    merged = merge_codes(existing, new_items)
    save_codes(merged)
    log.info("Done. Total codes: %d  New this run: %d", len(merged), len(new_items))


if __name__ == "__main__":
    main()
