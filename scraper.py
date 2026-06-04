#!/usr/bin/env python3
"""
scraper.py

Facebook posts for both Gaminator and Slotpark always use one of:
  CODE: <token>
  BONUS CODE: <token>

We use that exact pattern for Facebook.  Other sources (taplink, tiktok,
instagram) use the broader context regex as before.

post_date is scraped alongside each code so the UI shows "posted X ago".
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
# PATTERNS
# --------------------------------------------------------------------------

# Facebook-specific: only "CODE: X" or "BONUS CODE: X"
FB_CODE_RE = re.compile(
    r'(?:bonus\s+code|code)\s*:\s*([A-Za-z0-9]{3,12})',
    re.IGNORECASE,
)

# General context pattern for taplink / tiktok / instagram
GENERAL_CODE_RE = re.compile(
    r'(?:free\s*code|promo\s*code|bonus\s*code|coupon\s*code'
    r'|redeem\s*code|gift\s*code|reward\s*code'
    r'|promo|coupon|redeem|gift|reward|code)'
    r'[\s:=\-\u2013\u2014\u25ba\u2192\u00bb]+'
    r'([A-Za-z0-9]{3,12})'
    r'(?=[\s,;.!\)\]\"\u2019]|$)',
    re.IGNORECASE | re.MULTILINE,
)

BLACKLIST = {
    "HTTPS","HTTP","WWW","COM","NET","ORG","APP","APK",
    "FACEBOOK","INSTAGRAM","TIKTOK","TAPLINK",
    "GAMINATOR","SLOTPARK",
    "BONUS","PROMO","CODE","CODES",
    "FREE","COINS","CHIP","CHIPS","SPIN","SPINS",
    "DAILY","TODAY","WEEK","MONTH","NEW","GET","WIN",
    "PLAY","MORE","JOIN","LIKE","SHARE","FOLLOW","CLICK",
    "DOWNLOAD","INSTALL","UPDATE","LOGIN","REGISTER",
    "CEST","CET","UTC","GMT","PM","AM",
    "HERE","LINK","NOW","BELOW","ABOVE",
}


def _clean(token: str) -> str | None:
    t = token.upper()
    if t in BLACKLIST:               return None
    if t.isdigit():                  return None
    if re.fullmatch(r'\d+[KMBkmb]', t): return None
    if len(t) < 3:                   return None
    return t


def extract_codes_fb(text: str) -> list[str]:
    """Facebook: only CODE: / BONUS CODE: patterns."""
    found = set()
    for m in FB_CODE_RE.finditer(text):
        t = _clean(m.group(1))
        if t:
            log.info("  [FB code] %s  <- '%s'", t, m.group(0)[:50])
            found.add(t)
    return sorted(found)


def extract_codes_general(text: str) -> list[str]:
    """General context extraction for non-Facebook sources."""
    found = set()
    for m in GENERAL_CODE_RE.finditer(text):
        t = _clean(m.group(1))
        if t:
            log.info("  [code] %s  <- '%s'", t, m.group(0)[:50])
            found.add(t)
    return sorted(found)


# --------------------------------------------------------------------------
# DATE HELPERS
# --------------------------------------------------------------------------

REL_DATE_RE = re.compile(r'(\d+)\s*(second|minute|hour|day|week|month|year)s?\s*ago', re.I)
MONTH_MAP = {
    'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,
    'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12,
}


def parse_relative_date(text: str) -> str | None:
    m = REL_DATE_RE.search(text)
    if not m: return None
    n, unit = int(m.group(1)), m.group(2).lower()
    deltas = {'second':timedelta(seconds=n),'minute':timedelta(minutes=n),
              'hour':timedelta(hours=n),'day':timedelta(days=n),
              'week':timedelta(weeks=n),'month':timedelta(days=n*30),'year':timedelta(days=n*365)}
    return (datetime.now(timezone.utc) - deltas.get(unit, timedelta())).isoformat()


def parse_abs_date(text: str) -> str | None:
    now = datetime.now(timezone.utc)
    patterns = [
        (r'(\w+)\s+(\d{1,2}),?\s+(\d{4})', 'mdy'),
        (r'(\d{1,2})\s+(\w+)\s+(\d{4})', 'dmy'),
        (r'(\w+)\s+(\d{1,2})(?![\d,])', 'md'),
    ]
    for pat, fmt in patterns:
        m = re.search(pat, text)
        if not m: continue
        try:
            g = m.groups()
            if fmt == 'mdy':
                mon = MONTH_MAP.get(g[0][:3].lower()); day = int(g[1]); yr = int(g[2])
            elif fmt == 'dmy':
                day = int(g[0]); mon = MONTH_MAP.get(g[1][:3].lower()); yr = int(g[2])
            else:
                mon = MONTH_MAP.get(g[0][:3].lower()); day = int(g[1]); yr = now.year
            if not mon: continue
            return datetime(yr, mon, day, 12, 0, tzinfo=timezone.utc).isoformat()
        except Exception:
            continue
    return None


def best_date(strings: list[str]) -> str | None:
    for s in strings:
        d = parse_relative_date(s)
        if d: return d
    for s in strings:
        d = parse_abs_date(s)
        if d: return d
    return None


# --------------------------------------------------------------------------
# ITEM BUILDER / PERSISTENCE
# --------------------------------------------------------------------------

def now_utc():  return datetime.now(timezone.utc)
def iso_now():  return now_utc().isoformat()
def make_id(game, code): return hashlib.md5(f"{game}:{code}".encode()).hexdigest()[:12]


def build_item(game, code, platform, url="", post_date=None):
    return {
        "id":          make_id(game, code),
        "game":        game,
        "code":        code,
        "sources":     [platform],
        "post_date":   post_date or iso_now(),
        "found_at":    iso_now(),
        "raw_sources": [{"platform": platform, "url": url}],
    }


def load_existing():
    if not OUTPUT_FILE.exists(): return []
    try:
        data = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
        return data.get("codes", []) if isinstance(data, dict) else data
    except Exception as e:
        log.warning("Could not read codes.json: %s", e); return []


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
            by_key[key] = item; continue
        cur = by_key[key]
        seen = {(s["platform"], s.get("url","")) for s in cur.get("raw_sources",[])}
        for src in item.get("raw_sources",[]):
            pair = (src["platform"], src.get("url",""))
            if pair not in seen:
                cur.setdefault("raw_sources",[]).append(src); seen.add(pair)
        cur["sources"] = sorted({s["platform"] for s in cur.get("raw_sources",[])})
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

def pw_get_html(page, url, wait_ms=4000):
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(wait_ms)
        html = page.content()
        log.info("  got %d bytes from %s", len(html), url[:60])
        return html
    except Exception as e:
        log.warning("pw_get_html failed %s: %s", url, e); return ""


def scrape_taplink_pw(page, game, url):
    log.info("[%s] Taplink", game)
    html = pw_get_html(page, url, wait_ms=5000)
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    log.info("  taplink text: %s", text[:300])
    results = []
    for code in extract_codes_general(text):
        results.append(build_item(game, code, "taplink", url, post_date=iso_now()))
    return results


def scrape_facebook_pw(page, game, url):
    """
    Use Playwright on mbasic Facebook.
    Only extract tokens matching 'CODE: X' or 'BONUS CODE: X'.
    Also scroll down to load more posts.
    """
    log.info("[%s] Facebook", game)
    mbasic = url.replace("www.facebook.com", "mbasic.facebook.com")
    results = []
    try:
        page.goto(mbasic, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        # Scroll / click "See more" a few times to get more posts
        for _ in range(3):
            page.keyboard.press("End")
            page.wait_for_timeout(1500)

        html = page.content()
        soup = BeautifulSoup(html, "html.parser")

        # Each post on mbasic is a <div data-ft=...>
        posts = soup.find_all("div", attrs={"data-ft": True})
        log.info("  [%s] FB posts found: %d", game, len(posts))
        if not posts:
            # Login wall or empty — try whole-page text as fallback
            text = soup.get_text(" ", strip=True)
            log.info("  [%s] FB fallback text: %s", game, text[:300])
            for code in extract_codes_fb(text):
                results.append(build_item(game, code, "facebook", url))
            return results

        for post in posts:
            block_text = post.get_text(" ", strip=True)
            codes = extract_codes_fb(block_text)
            if not codes:
                continue
            # Grab date candidates from this post block
            date_candidates = []
            for abbr in post.find_all("abbr"):
                date_candidates.append(abbr.get("title","") or abbr.get_text())
            for span in post.find_all("span"):
                t = span.get_text(strip=True)
                if re.search(r'(ago|\d{4}|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)', t, re.I):
                    date_candidates.append(t)
            post_date = best_date(date_candidates)
            for code in codes:
                log.info("  [%s] FB -> %s (post_date=%s)", game, code, post_date)
                results.append(build_item(game, code, "facebook", url, post_date=post_date))

    except Exception as e:
        log.warning("[%s] Facebook failed: %s", game, e)
    return results


def scrape_instagram_pw(page, game, username):
    log.info("[%s] Instagram @%s", game, username)
    url = f"https://www.instagram.com/{username}/"
    html = pw_get_html(page, url, wait_ms=5000)
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    log.info("  IG text: %s", text[:300])
    results = []
    for code in extract_codes_general(text):
        results.append(build_item(game, code, "instagram", url, post_date=iso_now()))
    return results


def scrape_tiktok_pw(page, game, username):
    log.info("[%s] TikTok @%s", game, username)
    url = f"https://www.tiktok.com/@{username}"
    html = pw_get_html(page, url, wait_ms=6000)
    soup = BeautifulSoup(html, "html.parser")

    # Try per-video containers first
    video_items = (
        soup.find_all(attrs={"data-e2e": re.compile(r'user-post', re.I)}) or
        soup.find_all("div", class_=re.compile(r'DivItemContainer', re.I))
    )
    results = []
    if not video_items:
        text = soup.get_text(" ", strip=True)
        log.info("  TT page text: %s", text[:300])
        for code in extract_codes_general(text):
            results.append(build_item(game, code, "tiktok", url))
        return results

    for item in video_items:
        block_text = item.get_text(" ", strip=True)
        codes = extract_codes_general(block_text)
        if not codes: continue
        date_candidates = []
        for el in item.find_all(["span","p","time"]):
            t = el.get("datetime") or el.get_text(strip=True)
            if t and re.search(r'(ago|\d{4}|\d+-\d+-\d+)', t, re.I):
                date_candidates.append(t)
        post_date = best_date(date_candidates)
        for code in codes:
            log.info("  TT -> %s (post_date=%s)", code, post_date)
            results.append(build_item(game, code, "tiktok", url, post_date=post_date))
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
    log.info("Done. Total: %d  New this run: %d", len(merged), len(new_items))


if __name__ == "__main__":
    main()
