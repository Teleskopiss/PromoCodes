#!/usr/bin/env python3
"""
scraper.py
Scrapes promo codes for Gaminator and Slotpark from:
- Facebook posts/comments (public text only, heuristic scraping)
- Instagram bio + recent posts/comments
- TikTok bio + recent post text
- Taplink bonus area

Output: codes.json
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

try:
    from instagrapi import Client as InstaClient
    INSTAGRAPI_AVAILABLE = True
except ImportError:
    INSTAGRAPI_AVAILABLE = False


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

OUTPUT_FILE = Path("codes.json")
MAX_DISPLAY_AGE_DAYS = 5
MAX_HISTORY_DAYS = 30

GAMES = {
    "gaminator": {
        "facebook_url": "https://www.facebook.com/share/1BJaeBaKWZ/",
        "instagram_user": "gaminator",
        "tiktok_user": "gaminator3000",
        "taplink_url": "https://taplink.cc/gaminator3000",
    },
    "slotpark": {
        "facebook_url": "https://www.facebook.com/share/1U86tJdyE9/",
        "instagram_user": "slotpark",
        "tiktok_user": "slotparkslots",
        "taplink_url": None,
    },
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

BLACKLIST = {
    "HTTPS", "HTTP", "WWW", "COM", "NET", "ORG",
    "FACEBOOK", "INSTAGRAM", "TIKTOK", "GAMINATOR", "SLOTPARK",
    "BONUS", "PROMO", "CODE", "CODES", "SLOTS", "GAMES", "REELS",
    "STORY", "VIDEO", "POST", "COMMENT", "LINK", "BIO",
    "FREE", "COINS", "CHIP", "CHIPS", "SPIN", "SPINS",
    "DAILY", "TODAY", "WEEK", "MONTH", "NEW", "GET", "WIN",
    "PLAY", "MORE", "JOIN", "LIKE", "SHARE", "FOLLOW",
}

# Promo codes for these games are short alphanumeric tokens:
# - 3 to 8 characters
# - mix of letters and digits (not purely numeric, not purely alpha if >4 chars)
# - lowercase or uppercase, stored uppercase
# Examples: k8n95, mnv5, AB12C, X7Y2Z
CODE_PATTERN = re.compile(r"\b([A-Z0-9]{3,8})\b")


def now_utc():
    return datetime.now(timezone.utc)


def iso_now():
    return now_utc().isoformat()


def make_id(game: str, code: str) -> str:
    return hashlib.md5(f"{game}:{code}".encode()).hexdigest()[:12]


def normalize_code(code: str) -> str:
    return code.strip().upper()


def looks_like_promo(code: str) -> bool:
    """Heuristic: a real promo code has both letters and digits mixed in."""
    has_letter = any(c.isalpha() for c in code)
    has_digit  = any(c.isdigit() for c in code)
    # 3-4 char codes: just need to be alphanumeric mix
    # 5-8 char codes: must have both letters and digits
    if len(code) <= 4:
        return has_letter and has_digit
    return has_letter and has_digit


def extract_codes(text: str) -> list[str]:
    if not text:
        return []
    matches = CODE_PATTERN.findall(text.upper())
    out = []
    for m in matches:
        m = normalize_code(m)
        if m in BLACKLIST:
            continue
        if m.isdigit():
            continue
        if m.isalpha() and len(m) > 4:
            # Pure word longer than 4 chars — likely not a code
            continue
        if not looks_like_promo(m):
            continue
        out.append(m)
    return sorted(set(out))


def load_existing():
    if not OUTPUT_FILE.exists():
        return []
    try:
        return json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("Could not read codes.json: %s", e)
        return []


def save_codes(items: list[dict]):
    # Also write a metadata wrapper so the frontend knows when it was last updated
    output = {
        "updated_at": iso_now(),
        "codes": items
    }
    OUTPUT_FILE.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def load_codes_list(data) -> list[dict]:
    """Handle both old flat list and new {updated_at, codes} format."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("codes", [])
    return []


def merge_codes(existing: list[dict], new_items: list[dict]) -> list[dict]:
    by_key = {}

    for item in existing:
        key = f"{item['game']}::{item['code']}"
        by_key[key] = item

    for item in new_items:
        key = f"{item['game']}::{item['code']}"
        if key not in by_key:
            by_key[key] = item
            continue

        current = by_key[key]
        seen = {(s["platform"], s.get("url", "")) for s in current.get("raw_sources", [])}
        for src in item.get("raw_sources", []):
            pair = (src["platform"], src.get("url", ""))
            if pair not in seen:
                current.setdefault("raw_sources", []).append(src)
                seen.add(pair)

        current["sources"] = sorted({
            src["platform"] for src in current.get("raw_sources", [])
        })

        if item["found_at"] > current["found_at"]:
            current["found_at"] = item["found_at"]

    cutoff = now_utc() - timedelta(days=MAX_HISTORY_DAYS)
    merged = []
    for item in by_key.values():
        try:
            found_at = datetime.fromisoformat(item["found_at"])
            if found_at >= cutoff:
                merged.append(item)
        except Exception:
            merged.append(item)

    merged.sort(key=lambda x: x["found_at"], reverse=True)
    return merged


def build_item(game: str, code: str, platform: str, url: str = "") -> dict:
    return {
        "id": make_id(game, code),
        "game": game,
        "code": code,
        "sources": [platform],
        "found_at": iso_now(),
        "raw_sources": [
            {
                "platform": platform,
                "url": url
            }
        ]
    }


def scrape_taplink(game: str, url: str) -> list[dict]:
    log.info("[%s] Taplink: %s", game, url)
    results = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text(" ", strip=True)
        for code in extract_codes(text):
            results.append(build_item(game, code, "taplink", url))
    except Exception as e:
        log.warning("[%s] Taplink failed: %s", game, e)
    return results


def scrape_facebook(game: str, url: str) -> list[dict]:
    log.info("[%s] Facebook: %s", game, url)
    results = []

    mbasic_url = url.replace("www.facebook.com", "mbasic.facebook.com")
    pages_checked = 0
    next_url = mbasic_url

    while next_url and pages_checked < 3:
        try:
            resp = requests.get(next_url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            blocks = soup.find_all(["article", "div", "p"])
            for block in blocks:
                text = block.get_text(" ", strip=True)
                if not text:
                    continue
                codes = extract_codes(text)
                for code in codes:
                    platform = "facebook post"
                    low = text.lower()
                    if "comment" in low or "reply" in low:
                        platform = "facebook comment"
                    results.append(build_item(game, code, platform, url))

            next_link = soup.find("a", string=re.compile(r"(see more|more stories|older|next)", re.I))
            if next_link and next_link.get("href"):
                href = next_link["href"]
                next_url = f"https://mbasic.facebook.com{href}" if href.startswith("/") else href
            else:
                next_url = None

            pages_checked += 1
            time.sleep(1.5)

        except Exception as e:
            log.warning("[%s] Facebook failed on page %s: %s", game, next_url, e)
            break

    unique = {}
    for item in results:
        key = (item["code"], item["raw_sources"][0]["platform"])
        unique[key] = item
    return list(unique.values())


def scrape_instagram(game: str, username: str) -> list[dict]:
    log.info("[%s] Instagram: @%s", game, username)
    results = []
    seen = set()

    def add(code: str, platform: str, url: str = ""):
        key = (code, platform)
        if key in seen:
            return
        seen.add(key)
        results.append(build_item(game, code, platform, url or f"https://www.instagram.com/{username}/"))

    try:
        public_url = f"https://www.instagram.com/{username}/"
        resp = requests.get(public_url, headers=HEADERS, timeout=20)
        resp.raise_for_status()

        bio_match = re.search(r'"biography":"([^"]*)"', resp.text)
        if bio_match:
            bio = bio_match.group(1).replace("\\n", " ")
            for code in extract_codes(bio):
                add(code, "instagram bio", public_url)
    except Exception as e:
        log.warning("[%s] Instagram public bio failed: %s", game, e)

    if INSTAGRAPI_AVAILABLE:
        import os
        ig_user = os.getenv("IG_USERNAME")
        ig_pass = os.getenv("IG_PASSWORD")

        if ig_user and ig_pass:
            try:
                cl = InstaClient()
                cl.login(ig_user, ig_pass)

                user = cl.user_info_by_username(username)
                user_id = user.pk
                cutoff = now_utc() - timedelta(days=MAX_DISPLAY_AGE_DAYS)

                medias = cl.user_medias(user_id, amount=25)
                for media in medias:
                    taken = media.taken_at
                    if taken.tzinfo is None:
                        taken = taken.replace(tzinfo=timezone.utc)
                    if taken < cutoff:
                        continue

                    if media.caption_text:
                        for code in extract_codes(media.caption_text):
                            add(code, "instagram post", public_url)

                    try:
                        comments = cl.media_comments(media.pk, amount=40)
                        for c in comments:
                            if str(c.user.pk) == str(user_id):
                                for code in extract_codes(c.text or ""):
                                    add(code, "instagram comment", public_url)
                    except Exception:
                        pass

                cl.logout()

            except Exception as e:
                log.warning("[%s] instagrapi failed: %s", game, e)

    return results


def scrape_tiktok(game: str, username: str) -> list[dict]:
    log.info("[%s] TikTok: @%s", game, username)
    results = []
    seen = set()
    profile_url = f"https://www.tiktok.com/@{username}"

    def add(code: str, platform: str, url: str = ""):
        key = (code, platform)
        if key in seen:
            return
        seen.add(key)
        results.append(build_item(game, code, platform, url or profile_url))

    headers = dict(HEADERS)
    headers["Referer"] = "https://www.tiktok.com/"

    try:
        resp = requests.get(profile_url, headers=headers, timeout=20)
        resp.raise_for_status()
        html = resp.text

        bio_match = re.search(r'"signature":"([^"]*)"', html)
        if bio_match:
            bio = bio_match.group(1).replace("\\n", " ")
            for code in extract_codes(bio):
                add(code, "tiktok bio", profile_url)

        descs = re.findall(r'"desc":"([^"]*)"', html)
        times = re.findall(r'"createTime":(\d+)', html)
        cutoff_ts = (now_utc() - timedelta(days=MAX_DISPLAY_AGE_DAYS)).timestamp()

        for i, desc in enumerate(descs):
            ts = int(times[i]) if i < len(times) else int(now_utc().timestamp())
            if ts < cutoff_ts:
                continue
            for code in extract_codes(desc):
                add(code, "tiktok post", profile_url)

    except Exception as e:
        log.warning("[%s] TikTok failed: %s", game, e)

    return results


def main():
    raw = load_existing()
    existing = load_codes_list(raw)
    new_items = []

    for game, cfg in GAMES.items():
        log.info("=== %s ===", game)

        try:
            new_items.extend(scrape_facebook(game, cfg["facebook_url"]))
        except Exception as e:
            log.error("[%s] Facebook error: %s", game, e)
        time.sleep(2)

        try:
            new_items.extend(scrape_instagram(game, cfg["instagram_user"]))
        except Exception as e:
            log.error("[%s] Instagram error: %s", game, e)
        time.sleep(2)

        try:
            new_items.extend(scrape_tiktok(game, cfg["tiktok_user"]))
        except Exception as e:
            log.error("[%s] TikTok error: %s", game, e)
        time.sleep(2)

        if cfg.get("taplink_url"):
            try:
                new_items.extend(scrape_taplink(game, cfg["taplink_url"]))
            except Exception as e:
                log.error("[%s] Taplink error: %s", game, e)
            time.sleep(1)

    merged = merge_codes(existing, new_items)
    save_codes(merged)

    log.info("Saved %d total codes (%d found this run)", len(merged), len(new_items))


if __name__ == "__main__":
    main()
