import json
import re
import os
from datetime import datetime, timezone
from html import unescape
from bs4 import BeautifulSoup
import requests

CODES_FILE = "codes.json"

SOURCES = {
    "gaminator": {
        "url": "https://coinscrazy.com/gaminator-free-coins/",
        "label": "coinscrazy.com"
    },
    "slotpark": {
        "url": "https://coinscrazy.com/slotpark-bonus-code-free-chips/",
        "label": "coinscrazy.com"
    }
}

GAMINATOR_SITE = {
    "url": "https://gaminator.com/en/promotions",
    "label": "gaminator.com"
}

EIGHT_BP_REWARD = {
    "url": "https://www.8bpreward.win/2025/11/gaminator-codes.html",
    "label": "8bpreward.win"
}

TAPLINK_GAMINATOR = {
    "url": "https://taplink.cc/gaminator3000",
    "label": "taplink.cc/gaminator"
}

TAPLINK_SLOTPARK = {
    "url": "https://taplink.cc/slotpark",
    "label": "taplink.cc/slotpark"
}

# Sources that have exactly ONE active code at a time.
# When a new code is found from these, ALL previous codes from that source
# are marked replaced=True.
SINGLE_CODE_SOURCES = {
    "8bpreward.win",
    "taplink.cc/gaminator",
    "taplink.cc/slotpark",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def fetch_html(url: str) -> str | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=25)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"  [fetch] failed for {url}: {e}")
        return None


def fetch_with_fallback(url: str) -> str | None:
    text = fetch_html(url)
    if text:
        has_content = bool(re.search(r"BONUS|CODE", text, re.IGNORECASE))
        if has_content:
            print(f"  [fetch] direct OK for {url}")
            return unescape(text)
        print(f"  [fetch] direct had no useful content, trying cache")

    cache_url = f"https://webcache.googleusercontent.com/search?q=cache:{url}&hl=en"
    try:
        resp = requests.get(cache_url, headers=HEADERS, timeout=25)
        resp.raise_for_status()
        print(f"  [fetch] Google cache OK for {url}")
        return unescape(resp.text)
    except Exception as e:
        print(f"  [fetch] Google cache also failed: {e}")
    return None


def parse_date_ordinal(text: str) -> str | None:
    clean = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", text, flags=re.IGNORECASE).strip()
    for fmt in ("%d %B %Y", "%d %b %Y", "%B %d %Y"):
        try:
            return datetime.strptime(clean, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse_date_plain(text: str) -> str | None:
    text = text.strip()
    for fmt in ("%d %B %Y", "%B %d, %Y", "%d %b %Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# Words that are never promo codes
_BLACKLIST = {
    "THIS", "THE", "AND", "FOR", "ARE", "NOT", "YOU", "CAN",
    "ALL", "ANY", "USE", "NEW", "GET", "OUR", "HAS", "ITS",
    "WITH", "FROM", "THAT", "HAVE", "WILL", "THEY", "BEEN",
    "WERE", "ALSO", "INTO", "YOUR", "THEIR", "WHAT", "WHEN",
    "CODE", "BONUS", "CODES", "LINK", "GAME", "GAMES", "PLAY",
    "SAVE", "PAGE", "SITE", "DAILY", "FREE", "COINS", "CHIP",
    "CHIPS", "SPIN", "SPINS", "ENTER", "CLICK", "OPEN", "HERE",
    "MORE", "EACH", "SOME", "THAN", "THEN", "ONLY", "JUST",
    "LIKE", "KNOW", "MAKE", "TAKE", "GIVE", "FIND", "SHOW",
    "NEED", "WANT", "HELP", "WORK", "USED", "COME", "SOON",
    "BUTTON", "BELOW", "ABOVE", "TODAY", "NIGHT", "NOON",
    "WAIT", "BACK", "NEXT", "PREV", "LAST", "FIRST", "DONE",
    "ACTIVE", "VALID", "CHECK", "SHARE", "COPY", "PASTE",
    "CLAIM", "GRAB", "VIEW", "INFO", "DATA", "TEXT", "TYPE",
    "STEP", "NOTE", "READ", "SEND", "SIGN", "JOIN", "CALL",
    "VISIT", "ENJOY", "EARN", "REDEEM", "APPLY", "PRESS",
    "BONUS", "PROMO", "REWARD", "GIFTS", "GIFT",
}


def _looks_like_code(token: str) -> bool:
    """
    Returns True if the token could plausibly be a promo code:
    - 2-15 chars, alphanumeric
    - Not pure digits (codes always have at least one letter)
    - Not blacklisted
    - Has at least one letter
    """
    if token.upper() in _BLACKLIST:
        return False
    if not re.search(r"[A-Za-z]", token):  # must have at least one letter
        return False
    if len(token) < 2 or len(token) > 15:
        return False
    return True


def extract_bonus_code_from_game_announce(soup: BeautifulSoup) -> str | None:
    """
    Extracts the active bonus code from <div class="game-announce">.

    The site writes things like:
        "BONUS CODE: ra1n"
        "BONUS CODE ra1n"
        "Bonus Code:ra1n"
        "This page's BONUS CODE: ra1n"

    Strategy:
    1. Find the text of div.game-announce.
    2. Look for the pattern: BONUS CODE (optional sep) <token>
       BUT skip if the matched token is blacklisted — then keep scanning
       forward for the next token.
    3. Fallback: scan all tokens in the div for one that looks like a code.
    """
    announce = soup.find("div", class_="game-announce")
    target_divs = []
    if announce:
        target_divs.append(announce)
        print(f"  [8bpreward] div.game-announce found")
    else:
        print("  [8bpreward] div.game-announce NOT found — trying fallback divs")

    # Also check divs whose class contains 'announce' or 'bonus'
    for div in soup.find_all("div", class_=re.compile(r"announce|bonus", re.IGNORECASE)):
        if div not in target_divs:
            target_divs.append(div)

    for div in target_divs:
        text = div.get_text(" ", strip=True)
        print(f"  [8bpreward] scanning div text: {text!r}")

        # Find all occurrences of BONUS CODE (or just CODE) followed by a token
        # We iterate all matches so we can skip blacklisted ones
        for m in re.finditer(
            r"(?:BONUS\s+)?CODE\s*[:\-]?\s*([A-Za-z0-9]{2,15})",
            text, re.IGNORECASE
        ):
            token = m.group(1).strip()
            if _looks_like_code(token):
                print(f"  [8bpreward] code found (pattern match): {token!r}")
                return token
            else:
                print(f"  [8bpreward] skipping blacklisted/invalid token: {token!r}")

        # Fallback: scan all alphanumeric tokens in the div
        # Use the text after the last occurrence of 'CODE' or 'BONUS'
        after_code = re.split(r"(?:BONUS\s+)?CODE\s*[:\-]?", text, flags=re.IGNORECASE)
        if len(after_code) > 1:
            # Take the part right after the last 'CODE' keyword
            tail = after_code[-1].strip()
            tokens = re.findall(r"[A-Za-z0-9]{2,15}", tail)
            for token in tokens:
                if _looks_like_code(token):
                    print(f"  [8bpreward] code found (tail scan): {token!r}")
                    return token

        # Last resort: any token in the whole div that looks like a code
        # (avoids returning navigation words like 'home', 'click' etc)
        tokens = re.findall(r"[A-Za-z0-9]{2,15}", text)
        for token in reversed(tokens):  # reversed = take the last meaningful word
            if _looks_like_code(token):
                print(f"  [8bpreward] code found (last-token fallback): {token!r}")
                return token

    print("  [8bpreward] no code found in any announce/bonus div")
    return None


def scrape_8bpreward() -> tuple[list, dict]:
    """
    Returns (entries, date_to_link_count) where:
    - entries: list of code/pending dicts
    - date_to_link_count: {date_str: int} count of COLLECT links per date
      (used to compare against coinscrazy to decide if pending entries are needed)
    """
    print("[8bpreward] fetching...")
    html = fetch_html(EIGHT_BP_REWARD["url"])
    if not html:
        print("[8bpreward] could not fetch page")
        return [], {}

    soup = BeautifulSoup(html, "html.parser")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    results = []

    # --- 1. Extract the active bonus code ---
    code = extract_bonus_code_from_game_announce(soup)
    if code:
        print(f"[8bpreward] active code: {code}")
        results.append({
            "code": code,
            "date": today,
            "source": EIGHT_BP_REWARD["label"],
            "pending": False
        })
    else:
        print("[8bpreward] no active code found")

    # --- 2. COLLECT links grouped by date ---
    date_pattern = re.compile(
        r"(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})",
        re.IGNORECASE
    )

    all_collect_links = [
        a for a in soup.find_all("a", href=True)
        if "gam.to" in a["href"] and a.get_text(strip=True).upper() == "COLLECT"
    ]
    print(f"  [8bpreward] {len(all_collect_links)} COLLECT link(s)")

    def find_date_for_link(link_tag) -> str | None:
        node = link_tag
        for _ in range(12):
            node = node.parent
            if node is None:
                break
            text = node.get_text(" ", strip=True)
            m = date_pattern.search(text)
            if m:
                parsed = parse_date_ordinal(m.group(1))
                if parsed:
                    return parsed
        return None

    date_to_links: dict[str, list] = {}
    for link in all_collect_links:
        link_date = find_date_for_link(link) or today
        date_to_links.setdefault(link_date, []).append(link)

    # Build count map for comparison with coinscrazy
    date_to_link_count = {d: len(ls) for d, ls in date_to_links.items()}

    real_code_dates = {r["date"] for r in results if not r.get("pending")}

    pending_keys = set()
    for link_date, links in sorted(date_to_links.items(), reverse=True):
        print(f"  [8bpreward] {link_date}: {len(links)} COLLECT link(s)")
        if link_date in real_code_dates:
            print(f"  [8bpreward] skipping pending for {link_date} — real code exists")
            continue
        for i, link in enumerate(links):
            slot_num = len(links) - i
            pending_key = f"{link_date}_{slot_num}"
            if pending_key not in pending_keys:
                pending_keys.add(pending_key)
                results.append({
                    "code": f"PENDING_{link_date}_{slot_num}",
                    "date": link_date,
                    "source": EIGHT_BP_REWARD["label"],
                    "pending": True,
                    "collect_url": link["href"]
                })

    real = [r for r in results if not r.get("pending")]
    pend = [r for r in results if r.get("pending")]
    print(f"[8bpreward] done: {len(real)} real, {len(pend)} pending")
    return results, date_to_link_count


def extract_codes(raw: str, label: str) -> list:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    results = []
    seen = set()
    for m in re.finditer(
        r"(?:BONUS\s+)?CODE\s*[:\-=]\s*([A-Za-z0-9]{3,20})",
        raw, re.IGNORECASE
    ):
        code = m.group(1).strip()
        if code.upper() not in seen:
            seen.add(code.upper())
            results.append({"code": code, "date": today, "source": label, "pending": False})
    return results


def load_existing() -> dict:
    if os.path.exists(CODES_FILE):
        with open(CODES_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {
            "gaminator": raw.get("gaminator", []),
            "slotpark":  raw.get("slotpark", [])
        }
    return {"gaminator": [], "slotpark": []}


def save(data: dict):
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(CODES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def existing_codes(entries: list) -> set:
    return {e["code"].upper() for e in entries}


def scrape_coinscrazy(game: str) -> tuple[list, dict]:
    """
    Returns (entries, date_to_code_count) where:
    - entries: list of code dicts
    - date_to_code_count: {date_str: int} count of codes found per date
    """
    src = SOURCES[game]
    try:
        resp = requests.get(src["url"], headers=HEADERS, timeout=25)
        resp.raise_for_status()
    except Exception as e:
        print(f"[{game}] coinscrazy fetch error: {e}")
        return [], {}

    soup = BeautifulSoup(resp.text, "html.parser")
    content = soup.find("article") or soup.find("div", class_=re.compile("entry|post|content")) or soup.body
    if not content:
        print(f"[{game}] no content container found")
        return [], {}

    results = []
    current_date = None
    seen = set()
    date_to_code_count: dict[str, int] = {}

    for tag in content.find_all(True):
        text = tag.get_text(" ", strip=True)
        if not text:
            continue
        date_match = re.search(
            r"(?:Updated\s+On:\s*)?(\d{1,2}\s+[A-Za-z]+\s+\d{4})",
            text, re.IGNORECASE
        )
        if date_match:
            parsed = parse_date_plain(date_match.group(1))
            if parsed:
                current_date = parsed
        for m in re.finditer(r"CODE:-\s*([A-Za-z0-9]{2,12})", text, re.IGNORECASE):
            code = m.group(1).strip()
            if code.upper() not in seen:
                seen.add(code.upper())
                results.append({"code": code, "date": current_date, "source": src["label"], "pending": False})
                if current_date:
                    date_to_code_count[current_date] = date_to_code_count.get(current_date, 0) + 1

    print(f"[{game}] coinscrazy: {len(results)} codes, sample: {[r['code'] for r in results[:3]]}")
    return results, date_to_code_count


def scrape_gaminator_site() -> list:
    try:
        resp = requests.get(GAMINATOR_SITE["url"], headers=HEADERS, timeout=25)
        resp.raise_for_status()
    except Exception as e:
        print(f"[gaminator-site] fetch error: {e}")
        return []

    codes = re.findall(r"CODE:\s+([A-Za-z0-9]{2,12})", resp.text, re.IGNORECASE)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    results = []
    seen = set()
    for code in codes:
        code = code.strip()
        if code.upper() not in seen:
            seen.add(code.upper())
            results.append({"code": code, "date": today, "source": GAMINATOR_SITE["label"], "pending": False})
    print(f"[gaminator-site] {len(results)} codes")
    return results


def scrape_taplink_gaminator() -> list:
    print("[taplink-gaminator] fetching...")
    raw = fetch_with_fallback(TAPLINK_GAMINATOR["url"])
    if not raw:
        print("[taplink-gaminator] could not fetch page")
        return []
    all_codes = extract_codes(raw, TAPLINK_GAMINATOR["label"])
    result = all_codes[:1]  # Only the first/most prominent code
    print(f"[taplink-gaminator] active code: {[r['code'] for r in result]}")
    return result


def scrape_taplink_slotpark() -> list:
    print("[taplink-slotpark] fetching...")
    raw = fetch_with_fallback(TAPLINK_SLOTPARK["url"])
    if not raw:
        print("[taplink-slotpark] could not fetch page")
        return []
    all_codes = extract_codes(raw, TAPLINK_SLOTPARK["label"])
    result = all_codes[:1]  # Only the first/most prominent code
    print(f"[taplink-slotpark] active code: {[r['code'] for r in result]}")
    return result


def build_extra_pendings(date_to_8bp_count: dict, date_to_csz_count: dict,
                         existing_codes_set: set) -> list:
    """
    If 8bpreward has MORE collect links for a date than coinscrazy has codes,
    the difference represents codes that haven't been published on coinscrazy yet.
    Add a pending entry for each missing slot.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    extras = []
    for date, bp_count in date_to_8bp_count.items():
        csz_count = date_to_csz_count.get(date, 0)
        diff = bp_count - csz_count
        if diff > 0:
            print(f"  [pending-compare] {date}: 8bpreward={bp_count}, coinscrazy={csz_count}, extra pending={diff}")
            for i in range(diff):
                slot_num = i + 1
                key = f"XPENDING_{date}_{slot_num}"
                if key.upper() not in existing_codes_set:
                    extras.append({
                        "code": key,
                        "date": date,
                        "source": "8bpreward.win",
                        "pending": True,
                        "collect_url": None
                    })
    return extras


def merge(existing: list, new_entries: list) -> tuple:
    """
    Merges new scraped entries into the existing list.

    Single-code source rules (taplink.cc/gaminator, taplink.cc/slotpark, 8bpreward.win):
    - Only ONE non-replaced, non-pending code allowed per source at a time.
    - When a new code arrives, ALL previous active codes from that source
      are marked replaced=True, replaced_at=now.
    - Pending entries for a date are removed once a real code for that date exists.
    """
    now = datetime.now(timezone.utc).isoformat()
    added = 0

    known_codes = existing_codes(existing)

    real_dates = {e["date"] for e in existing if not e.get("pending")}
    for e in new_entries:
        if not e.get("pending"):
            real_dates.add(e.get("date"))

    for entry in new_entries:
        code_key = entry["code"].upper()
        src = entry.get("source", "")

        if entry.get("pending"):
            # Don't add pending if a real code for same date already exists
            if entry.get("date") in real_dates:
                continue
            if code_key in known_codes:
                continue
        else:
            if code_key in known_codes:
                # Already known — skip
                continue

            # Single-code source: mark ALL previous active codes from this source as replaced
            if src in SINGLE_CODE_SOURCES:
                for old in existing:
                    if (old.get("source") == src
                            and not old.get("pending")
                            and not old.get("replaced")
                            and old["code"].upper() != code_key):
                        print(f"  [merge] {src}: marking '{old['code']}' as replaced by '{entry['code']}'")
                        old["replaced"] = True
                        old["replaced_at"] = now

        existing.append({
            "code":        entry["code"],
            "date":        entry.get("date"),
            "source":      entry.get("source"),
            "pending":     entry.get("pending", False),
            "collect_url": entry.get("collect_url"),
            "found_at":    now
        })
        known_codes.add(code_key)
        added += 1

    # Remove pending entries superseded by real codes
    before = len(existing)
    existing = [
        e for e in existing
        if not (e.get("pending") and e.get("date") in real_dates)
    ]
    removed = before - len(existing)
    if removed:
        print(f"  [merge] removed {removed} pending(s) superseded by real codes")

    return existing, added


def repair_single_code_sources(data: dict) -> dict:
    """
    One-time repair: ensure each SINGLE_CODE_SOURCE has at most one
    non-replaced, non-pending code per game. Mark all but the most
    recent (by found_at) as replaced.
    """
    now = datetime.now(timezone.utc).isoformat()
    for game in ("gaminator", "slotpark"):
        for src in SINGLE_CODE_SOURCES:
            actives = [
                e for e in data[game]
                if e.get("source") == src
                and not e.get("pending")
                and not e.get("replaced")
            ]
            if len(actives) > 1:
                # Sort by found_at ascending; all but last get replaced
                actives.sort(key=lambda e: e.get("found_at") or e.get("date") or "")
                for old in actives[:-1]:
                    print(f"  [repair] {game}/{src}: marking old code '{old['code']}' as replaced")
                    old["replaced"] = True
                    old["replaced_at"] = now
    return data


def main():
    data = load_existing()

    # Repair any existing data where multiple codes from same single-code source are active
    data = repair_single_code_sources(data)

    total_added = 0

    # Scrape gaminator sources
    csz_gaminator, csz_gam_date_counts = scrape_coinscrazy("gaminator")
    bp_entries, bp_date_counts = scrape_8bpreward()
    tap_gam = scrape_taplink_gaminator()
    gam_site = scrape_gaminator_site()

    # Extra pending entries: 8bpreward has more links than coinscrazy has codes
    existing_set = existing_codes(data["gaminator"])
    extra_pendings = build_extra_pendings(bp_date_counts, csz_gam_date_counts, existing_set)
    if extra_pendings:
        print(f"  [pending-compare] adding {len(extra_pendings)} extra pending(s)")

    gaminator_new = csz_gaminator + gam_site + bp_entries + tap_gam + extra_pendings
    data["gaminator"], added = merge(data["gaminator"], gaminator_new)
    total_added += added
    print(f"[gaminator] +{added} (total: {len(data['gaminator'])})")

    # Scrape slotpark sources
    csz_slotpark, _ = scrape_coinscrazy("slotpark")
    tap_slp = scrape_taplink_slotpark()

    slotpark_new = csz_slotpark + tap_slp
    data["slotpark"], added = merge(data["slotpark"], slotpark_new)
    total_added += added
    print(f"[slotpark] +{added} (total: {len(data['slotpark'])})")

    save(data)
    print(f"Done. Total added: {total_added}")


if __name__ == "__main__":
    main()
