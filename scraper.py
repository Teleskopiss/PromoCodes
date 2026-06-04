#!/usr/bin/env python3
"""
scraper.py

Extraction rules:
- Facebook: ONLY tokens that appear immediately after "CODE:" or "BONUS CODE:"
  No fallback. No bare-token scan. If the label isn't there, nothing is extracted.
- Other sources (taplink, instagram, tiktok): same strict CODE:/BONUS CODE: rule
  plus a few extra label words (promo, redeem, gift, reward).

post_date is scraped per-post so the UI shows "posted X ago".
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

# ------------------------------------------------------------------ #
# EXTRACTION                                                          #
# ------------------------------------------------------------------ #
# Strict pattern: token MUST follow "CODE:" or "BONUS CODE:"
# Captures only the token after the colon+optional-space.
# No fallback. No bare-token scan. No blacklist needed.
# ------------------------------------------------------------------ #

FB_CODE_RE = re.compile(
    r'(?:bonus\s+code|code)\s*:\s*([A-Za-z0-9]{3,12})',
    re.IGNORECASE,
)

# For non-FB sources we also accept a few extra label words
GENERAL_CODE_RE = re.compile(
    r'(?:bonus\s+code|promo\s+code|redeem\s+code|gift\s+code'
    r'|reward\s+code|free\s+code|code)'
    r'\s*:\s*([A-Za-z0-9]{3,12})',
    re.IGNORECASE,
)


def _valid(token: str) -> str | None:
    """Return uppercased token if it looks like a real code, else None."""
    t = token.strip().upper()
    # pure number
    if t.isdigit(): return None
    # number + K/M/B suffix (follower counts like 91K, 1M)
    if re.fullmatch(r'\d+[KMBkmb]', t): return None
    # too short
    if len(t) < 3: return None
    return t


def extract_fb(text: str) -> list[str]:
    found = set()
    for m in FB_CODE_RE.finditer(text):
        t = _valid(m.group(1))
        if t:
            log.info("  [FB] %s  <-- %r", t, m.group(0))
            found.add(t)
    return sorted(found)


def extract_general(text: str) -> list[str]:
    found = set()
    for m in GENERAL_CODE_RE.finditer(text):
        t = _valid(m.group(1))
        if t:
            log.info("  [general] %s  <-- %r", t, m.group(0))
            found.add(t)
    return sorted(found)


# ------------------------------------------------------------------ #
# DATE HELPERS                                                        #
# ------------------------------------------------------------------ #

REL_RE = re.compile(r'(\d+)\s*(second|minute|hour|day|week|month|year)s?\s*ago', re.I)
MON = {'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,
       'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}


def parse_rel(text):
    m = REL_RE.search(text)
    if not m: return None
    n, u = int(m.group(1)), m.group(2).lower()
    d = {'second':timedelta(seconds=n),'minute':timedelta(minutes=n),
         'hour':timedelta(hours=n),'day':timedelta(days=n),
         'week':timedelta(weeks=n),'month':timedelta(days=n*30),
         'year':timedelta(days=n*365)}.get(u, timedelta())
    return (datetime.now(timezone.utc) - d).isoformat()


def parse_abs(text):
    now = datetime.now(timezone.utc)
    for pat, order in [
        (r'(\w+)\s+(\d{1,2}),?\s+(\d{4})', 'mdy'),
        (r'(\d{1,2})\s+(\w+)\s+(\d{4})',   'dmy'),
        (r'(\w+)\s+(\d{1,2})(?![\d,])',     'md'),
    ]:
        m = re.search(pat, text)
        if not m: continue
        try:
            g = m.groups()
            if order == 'mdy':   mo=MON.get(g[0][:3].lower()); dy=int(g[1]); yr=int(g[2])
            elif order == 'dmy': dy=int(g[0]); mo=MON.get(g[1][:3].lower()); yr=int(g[2])
            else:                mo=MON.get(g[0][:3].lower()); dy=int(g[1]); yr=now.year
            if not mo: continue
            return datetime(yr, mo, dy, 12, 0, tzinfo=timezone.utc).isoformat()
        except Exception: continue
    return None


def best_date(strings):
    for s in strings:
        d = parse_rel(s)
        if d: return d
    for s in strings:
        d = parse_abs(s)
        if d: return d
    return None


# ------------------------------------------------------------------ #
# ITEM / PERSISTENCE                                                  #
# ------------------------------------------------------------------ #

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
        log.warning("load failed: %s", e); return []


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
            p = (src["platform"], src.get("url",""))
            if p not in seen:
                cur.setdefault("raw_sources",[]).append(src); seen.add(p)
        cur["sources"] = sorted({s["platform"] for s in cur.get("raw_sources",[])})
        if item.get("post_date") and item["post_date"] < cur.get("post_date", item["post_date"]):
            cur["post_date"] = item["post_date"]
    cutoff = now_utc() - timedelta(days=MAX_HISTORY_DAYS)
    merged = [v for v in by_key.values()
              if datetime.fromisoformat(v.get("found_at", iso_now())) >= cutoff]
    merged.sort(key=lambda x: x.get("post_date", x["found_at"]), reverse=True)
    return merged


# ------------------------------------------------------------------ #
# SCRAPERS                                                            #
# ------------------------------------------------------------------ #

def pw_html(page, url, wait_ms=4000):
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(wait_ms)
        return page.content()
    except Exception as e:
        log.warning("pw_html %s: %s", url, e); return ""


def scrape_taplink(page, game, url):
    log.info("[%s] Taplink", game)
    soup = BeautifulSoup(pw_html(page, url, 5000), "html.parser")
    text = soup.get_text(" ", strip=True)
    log.info("  taplink text snippet: %s", text[:200])
    return [build_item(game, c, "taplink", url, iso_now()) for c in extract_general(text)]


def scrape_facebook(page, game, url):
    """
    Playwright on mbasic.facebook.com.
    Only extracts tokens after CODE: or BONUS CODE:.
    NO fallback to whole-page text (avoids follower count false positives).
    """
    log.info("[%s] Facebook", game)
    mbasic = url.replace("www.facebook.com", "mbasic.facebook.com")
    results = []
    try:
        page.goto(mbasic, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        # scroll to load more posts
        for _ in range(3):
            page.keyboard.press("End")
            page.wait_for_timeout(1200)

        soup = BeautifulSoup(page.content(), "html.parser")

        # mbasic post blocks have data-ft attribute
        posts = soup.find_all("div", attrs={"data-ft": True})
        log.info("  [%s] FB post blocks: %d", game, len(posts))

        # if no posts found (login wall etc.), log and return empty — no fallback
        if not posts:
            log.warning("  [%s] FB: no post blocks found (login wall?). Skipping.", game)
            return []

        for post in posts:
            text = post.get_text(" ", strip=True)
            codes = extract_fb(text)
            if not codes:
                continue

            # date extraction for this post block
            dates = []
            for el in post.find_all("abbr"):
                dates.append(el.get("title","") or el.get_text())
            for el in post.find_all("span"):
                t = el.get_text(strip=True)
                if re.search(r'ago|\d{4}|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec', t, re.I):
                    dates.append(t)
            post_date = best_date(dates)

            for code in codes:
                log.info("  [%s] FB code: %s  post_date=%s", game, code, post_date)
                results.append(build_item(game, code, "facebook", url, post_date))

    except Exception as e:
        log.error("[%s] Facebook error: %s", game, e)
    return results


def scrape_instagram(page, game, username):
    log.info("[%s] Instagram @%s", game, username)
    url = f"https://www.instagram.com/{username}/"
    soup = BeautifulSoup(pw_html(page, url, 5000), "html.parser")
    text = soup.get_text(" ", strip=True)
    log.info("  IG snippet: %s", text[:200])
    return [build_item(game, c, "instagram", url, iso_now()) for c in extract_general(text)]


def scrape_tiktok(page, game, username):
    log.info("[%s] TikTok @%s", game, username)
    url = f"https://www.tiktok.com/@{username}"
    soup = BeautifulSoup(pw_html(page, url, 6000), "html.parser")

    items = (
        soup.find_all(attrs={"data-e2e": re.compile(r"user-post", re.I)}) or
        soup.find_all("div", class_=re.compile(r"DivItemContainer", re.I))
    )
    results = []
    if not items:
        text = soup.get_text(" ", strip=True)
        log.info("  TT page snippet: %s", text[:200])
        return [build_item(game, c, "tiktok", url) for c in extract_general(text)]

    for item in items:
        text = item.get_text(" ", strip=True)
        codes = extract_general(text)
        if not codes: continue
        dates = []
        for el in item.find_all(["span","p","time"]):
            t = el.get("datetime") or el.get_text(strip=True)
            if t and re.search(r'ago|\d{4}|\d+-\d+-\d+', t, re.I):
                dates.append(t)
        post_date = best_date(dates)
        for code in codes:
            results.append(build_item(game, code, "tiktok", url, post_date))
    return results


# ------------------------------------------------------------------ #
# MAIN                                                                #
# ------------------------------------------------------------------ #

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
            log.info("====== %s ======", game)

            if cfg.get("taplink_url"):
                try: new_items.extend(scrape_taplink(page, game, cfg["taplink_url"]))
                except Exception as e: log.error("taplink: %s", e)
                time.sleep(2)

            try: new_items.extend(scrape_facebook(page, game, cfg["facebook_url"]))
            except Exception as e: log.error("facebook: %s", e)
            time.sleep(2)

            try: new_items.extend(scrape_instagram(page, game, cfg["instagram_user"]))
            except Exception as e: log.error("instagram: %s", e)
            time.sleep(2)

            try: new_items.extend(scrape_tiktok(page, game, cfg["tiktok_user"]))
            except Exception as e: log.error("tiktok: %s", e)
            time.sleep(2)

        browser.close()

    merged = merge_codes(existing, new_items)
    save_codes(merged)
    log.info("Done. total=%d new=%d", len(merged), len(new_items))


if __name__ == "__main__":
    main()
