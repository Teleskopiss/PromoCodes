#!/usr/bin/env python3
"""
scraper.py - Uses Playwright for JS-rendered pages so codes are actually visible.
"""

import json
import re
import time
import hashlib
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

OUTPUT_FILE      = Path("codes.json")
MAX_HISTORY_DAYS = 30
MAX_AGE_DAYS     = 7

GAMES = {
    "gaminator": {
        "facebook_url":    "https://www.facebook.com/gaminator3000",
        "instagram_user":  "gaminator",
        "tiktok_user":     "gaminator3000",
        "taplink_url":     "https://taplink.cc/gaminator3000",
    },
    "slotpark": {
        "facebook_url":    "https://www.facebook.com/slotpark",
        "instagram_user":  "slotpark",
        "tiktok_user":     "slotparkslots",
        "taplink_url":     None,
    },
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

BLACKLIST = {
    "HTTPS","HTTP","WWW","COM","NET","ORG","APP","APK",
    "FACEBOOK","INSTAGRAM","TIKTOK","TAPLINK",
    "GAMINATOR","SLOTPARK",
    "BONUS","PROMO","CODE","CODES","SLOTS","GAMES","REELS",
    "STORY","VIDEO","POST","COMMENT","LINK","BIO",
    "FREE","COINS","CHIP","CHIPS","SPIN","SPINS",
    "DAILY","TODAY","WEEK","MONTH","NEW","GET","WIN",
    "PLAY","MORE","JOIN","LIKE","SHARE","FOLLOW","CLICK",
    "DOWNLOAD","INSTALL","UPDATE","LOGIN","REGISTER",
}

# Codes appear after context words like "code:", "promo:", "bonus:", etc.
# Primary pattern: look for codes preceded by a colon or label
# Also falls back to bare short alphanumeric tokens as secondary pass
CODE_CONTEXT_RE = re.compile(
    r'(?:code|promo|bonus|coupon|redeem|gift|reward)[\s:=\-]+([A-Za-z0-9]{3,8})\b',
    re.IGNORECASE
)
# Fallback: bare short mixed-alphanumeric token surrounded by whitespace/punctuation
CODE_BARE_RE = re.compile(r'(?:^|[\s,;\|\(\[\"\'])([A-Za-z0-9]{3,8})(?=[\s,;\|\)\]\"\']|$)')


def now_utc():  return datetime.now(timezone.utc)
def iso_now():  return now_utc().isoformat()
def make_id(game, code): return hashlib.md5(f"{game}:{code}".encode()).hexdigest()[:12]


def looks_like_code(token: str) -> bool:
    t = token.upper()
    if t in BLACKLIST:              return False
    if t.isdigit():                 return False
    # pure word longer than 4 chars is likely not a code
    if t.isalpha() and len(t) > 4: return False
    has_letter = any(c.isalpha() for c in t)
    has_digit  = any(c.isdigit() for c in t)
    return has_letter and has_digit


def extract_codes(text: str) -> list[str]:
    if not text:
        return []
    found = set()

    # Pass 1: high-confidence — code preceded by label+colon
    for m in CODE_CONTEXT_RE.finditer(text):
        token = m.group(1).upper()
        if looks_like_code(token):
            log.info("  [context match] %s", token)
            found.add(token)

    # Pass 2: bare tokens (lower confidence, more false positives filtered by looks_like_code)
    for m in CODE_BARE_RE.finditer(text):
        token = m.group(1).upper()
        if looks_like_code(token):
            found.add(token)

    return sorted(found)


def build_item(game, code, platform, url=""):
    return {
        "id":          make_id(game, code),
        "game":        game,
        "code":        code,
        "sources":     [platform],
        "found_at":    iso_now(),
        "raw_sources": [{"platform": platform, "url": url}],
    }


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
        encoding="utf-8"
    )


def merge_codes(existing, new_items):
    by_key = {f"{i['game']}::{i['code']}": i for i in existing}
    for item in new_items:
        key = f"{item['game']}::{item['code']}"
        if key not in by_key:
            by_key[key] = item
            continue
        cur = by_key[key]
        seen = {(s["platform"], s.get("url","")) for s in cur.get("raw_sources",[])}
        for src in item.get("raw_sources",[]):
            pair = (src["platform"], src.get("url",""))
            if pair not in seen:
                cur.setdefault("raw_sources",[]).append(src)
                seen.add(pair)
        cur["sources"] = sorted({s["platform"] for s in cur.get("raw_sources",[])})
        if item["found_at"] > cur["found_at"]: cur["found_at"] = item["found_at"]

    cutoff = now_utc() - timedelta(days=MAX_HISTORY_DAYS)
    merged = []
    for item in by_key.values():
        try:
            if datetime.fromisoformat(item["found_at"]) >= cutoff:
                merged.append(item)
        except Exception:
            merged.append(item)
    merged.sort(key=lambda x: x["found_at"], reverse=True)
    return merged


def pw_get_text(page, url: str, wait_ms: int = 4000) -> str:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(wait_ms)
        text = page.inner_text("body")
        log.info("  text sample: %s", text[:300].replace("\n", " "))
        return text
    except Exception as e:
        log.warning("pw_get_text failed for %s: %s", url, e)
        return ""


def scrape_taplink_pw(page, game, url):
    log.info("[%s] Taplink: %s", game, url)
    text = pw_get_text(page, url, wait_ms=5000)
    results = []
    for code in extract_codes(text):
        log.info("[%s] Taplink -> %s", game, code)
        results.append(build_item(game, code, "taplink", url))
    return results


def scrape_facebook(game, url):
    log.info("[%s] Facebook: %s", game, url)
    results = []
    mbasic = url.replace("www.facebook.com", "mbasic.facebook.com")
    try:
        resp = requests.get(mbasic, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text(" ", strip=True)
        log.info("[%s] FB sample: %s", game, text[:200])
        for code in extract_codes(text):
            log.info("[%s] Facebook -> %s", game, code)
            results.append(build_item(game, code, "facebook", url))
    except Exception as e:
        log.warning("[%s] Facebook failed: %s", game, e)
    return results


def scrape_instagram_pw(page, game, username):
    log.info("[%s] Instagram: @%s", game, username)
    url  = f"https://www.instagram.com/{username}/"
    text = pw_get_text(page, url, wait_ms=5000)
    results = []
    for code in extract_codes(text):
        log.info("[%s] Instagram -> %s", game, code)
        results.append(build_item(game, code, "instagram", url))
    return results


def scrape_tiktok_pw(page, game, username):
    log.info("[%s] TikTok: @%s", game, username)
    url  = f"https://www.tiktok.com/@{username}"
    text = pw_get_text(page, url, wait_ms=5000)
    results = []
    for code in extract_codes(text):
        log.info("[%s] TikTok -> %s", game, code)
        results.append(build_item(game, code, "tiktok", url))
    return results


def main():
    existing  = load_existing()
    new_items = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="en-US",
            viewport={"width": 1280, "height": 800},
        )
        page = ctx.new_page()

        for game, cfg in GAMES.items():
            log.info("=== %s ===", game)

            if cfg.get("taplink_url"):
                try: new_items.extend(scrape_taplink_pw(page, game, cfg["taplink_url"]))
                except Exception as e: log.error("[%s] Taplink error: %s", game, e)
                time.sleep(2)

            try: new_items.extend(scrape_facebook(game, cfg["facebook_url"]))
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
    log.info("Done. Total: %d  Found this run: %d", len(merged), len(new_items))


if __name__ == "__main__":
    main()
