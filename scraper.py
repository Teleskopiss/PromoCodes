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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def fetch_html(url: str) -> str | None:
    """Direct fetch returning raw HTML text, or None on failure."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=25)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"  [fetch] failed for {url}: {e}")
        return None


def fetch_with_fallback(url: str) -> str | None:
    """
    Try direct fetch first; if the page has no useful CODE/BONUS content,
    fall back to Google's cache.
    """
    text = fetch_html(url)
    if text:
        has_content = bool(re.search(r"BONUS|CODE", text, re.IGNORECASE))
        if has_content:
            print(f"  [fetch] direct OK for {url}")
            return unescape(text)
        print(f"  [fetch] direct had no useful content for {url}, trying cache")

    cache_url = f"https://webcache.googleusercontent.com/search?q=cache:{url}&hl=en"
    try:
        resp = requests.get(cache_url, headers=HEADERS, timeout=25)
        resp.raise_for_status()
        print(f"  [fetch] Google cache OK for {url}")
        return unescape(resp.text)
    except Exception as e:
        print(f"  [fetch] Google cache also failed for {url}: {e}")

    return None


def parse_date_ordinal(text: str) -> str | None:
    """
    Parse dates like '6th June 2026', '21st January 2026', '2nd March 2026'.
    Strips ordinal suffixes (st/nd/rd/th) then parses.
    """
    clean = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", text, flags=re.IGNORECASE).strip()
    for fmt in ("%d %B %Y", "%d %b %Y", "%B %d %Y"):
        try:
            return datetime.strptime(clean, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse_date_plain(text: str) -> str | None:
    """Parse dates like '22 January 2026', 'January 22, 2026', '22.01.2026'."""
    text = text.strip()
    for fmt in ("%d %B %Y", "%B %d, %Y", "%d %b %Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# Words that can appear after "BONUS CODE :" but are NOT actual codes.
# These come from the page description text, not the widget.
_BONUS_CODE_BLACKLIST = {
    "THIS", "THE", "AND", "FOR", "ARE", "NOT", "YOU", "CAN",
    "ALL", "ANY", "USE", "NEW", "GET", "OUR", "HAS", "ITS",
    "WITH", "FROM", "THAT", "HAVE", "WILL", "THEY", "BEEN",
    "WERE", "ALSO", "INTO", "YOUR", "THEIR", "WHAT", "WHEN",
    "CODE", "BONUS", "CODES", "LINK", "GAME", "GAMES", "PLAY",
    "SAVE", "PAGE", "SITE", "DAILY", "FREE", "COINS", "CHIP",
    "CHIPS", "SPIN", "SPINS", "ENTER", "CLICK", "OPEN", "HERE",
    "MORE", "EACH", "SOME", "THAN", "THEN", "ONLY", "JUST",
    "LIKE", "KNOW", "MAKE", "TAKE", "GIVE", "FIND", "SHOW",
    "NEED", "WANT", "HELP", "WORK", "USED", "BEEN", "COME",
}


def scrape_8bpreward() -> list:
    """
    Scrapes https://www.8bpreward.win/2025/11/gaminator-codes.html

    Two things to find:

    1. ACTUAL CODES — only from the exact pattern:
           BONUS CODE : <code>
       The page has a special widget box near the top that shows the current
       active code. We look for the text node right after the "NEW BONUS CODE"
       button label, specifically in the pattern:
           NEW BONUS CODE   BONUS CODE : ra1n
       We search in multiple ways:
         a) Look for elements containing "NEW BONUS CODE" and extract nearby text
         b) Scan the raw HTML source for the pattern before any HTML processing
         c) Fallback: scan soup text, but apply a strict blacklist to avoid
            matching words like "this" from description sentences

    2. PENDING CODES — the page often posts COLLECT button links (gam.to URLs)
       before any code text is published. For each date section, we count the
       active COLLECT links (not Expired). We emit one pending entry per link.
    """
    print("[8bpreward] fetching...")
    html = fetch_html(EIGHT_BP_REWARD["url"])
    if not html:
        print("[8bpreward] could not fetch page")
        return []

    soup = BeautifulSoup(html, "html.parser")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    results = []
    seen_codes = set()

    # ----------------------------------------------------------------
    # STRATEGY A: Scan raw HTML source directly for the widget pattern.
    #
    # The widget renders something like:
    #   <span ...>NEW BONUS CODE</span>BONUS CODE : ra1n
    # or inside a table cell:
    #   <td ...><span ...>NEW BONUS CODE</span>BONUS CODE : ra1n</td>
    #
    # By scanning the raw HTML we avoid any text-normalization issues
    # from BeautifulSoup's get_text() that might cause the regex to miss
    # or incorrectly split the match.
    #
    # We use a strict pattern that requires:
    #   - The text "BONUS CODE" (case-insensitive)
    #   - Optional whitespace
    #   - A colon ":"
    #   - Optional whitespace
    #   - The code: alphanumeric, 3–12 characters
    #   - Immediately followed by a word boundary (space, <, end of string)
    #     so we don't match partial HTML attribute values
    # ----------------------------------------------------------------
    raw_html_unescaped = unescape(html)

    # Strip HTML tags for a clean text pass on the raw source
    raw_text_only = re.sub(r"<[^>]+>", " ", raw_html_unescaped)
    raw_text_only = re.sub(r"\s+", " ", raw_text_only)

    print(f"  [8bpreward] raw text length: {len(raw_text_only)}")

    # Find all occurrences of "BONUS CODE : <something>"
    # We deliberately require "BONUS CODE" (both words) to avoid bare "CODE:"
    # matches in description text.
    raw_matches = list(re.finditer(
        r"BONUS\s+CODE\s*:\s*([A-Za-z0-9]{2,12})(?=[\s<,\.!\?]|$)",
        raw_text_only,
        re.IGNORECASE
    ))

    print(f"  [8bpreward] strategy A found {len(raw_matches)} BONUS CODE match(es) in raw text")

    for m in raw_matches:
        code = m.group(1).strip()
        code_upper = code.upper()

        # Skip blacklisted words — these appear in description sentences
        if code_upper in _BONUS_CODE_BLACKLIST:
            print(f"  [8bpreward] skipping blacklisted word: {code}")
            continue

        if code_upper not in seen_codes:
            seen_codes.add(code_upper)
            print(f"  [8bpreward] found BONUS CODE: {code}")
            results.append({
                "code": code,
                "date": today,
                "source": EIGHT_BP_REWARD["label"],
                "pending": False
            })

    # ----------------------------------------------------------------
    # STRATEGY B: Look for the "NEW BONUS CODE" widget element directly.
    #
    # The page has a styled element (span/div/td) with class or inline style
    # containing the text "NEW BONUS CODE". The actual code text appears
    # immediately adjacent (in the same parent container).
    #
    # We find that container, extract ALL text from it, and scan for
    # "BONUS CODE : <code>".
    # ----------------------------------------------------------------
    new_bonus_tags = soup.find_all(
        lambda tag: tag.get_text(strip=True).upper() == "NEW BONUS CODE"
    )
    print(f"  [8bpreward] strategy B: found {len(new_bonus_tags)} 'NEW BONUS CODE' element(s)")

    for tag in new_bonus_tags:
        # Check the parent container (up to 3 levels)
        container = tag
        for _ in range(3):
            container = container.parent
            if container is None:
                break
            container_text = container.get_text(" ", strip=True)
            for m in re.finditer(
                r"BONUS\s+CODE\s*:\s*([A-Za-z0-9]{2,12})(?=[\s,\.!\?]|$)",
                container_text,
                re.IGNORECASE
            ):
                code = m.group(1).strip()
                code_upper = code.upper()
                if code_upper in _BONUS_CODE_BLACKLIST:
                    print(f"  [8bpreward] skipping blacklisted word (B): {code}")
                    continue
                if code_upper not in seen_codes:
                    seen_codes.add(code_upper)
                    print(f"  [8bpreward] strategy B found code: {code}")
                    results.append({
                        "code": code,
                        "date": today,
                        "source": EIGHT_BP_REWARD["label"],
                        "pending": False
                    })

    # ----------------------------------------------------------------
    # PARSE DATE SECTIONS AND COUNT COLLECT BUTTONS
    #
    # Page structure (Blogger table layout):
    #   <table> containing a row with date heading e.g. "6th June 2026"
    #   followed by rows:
    #     # | GIFT  | LINK
    #     1 | Coins | <a href="https://gam.to/...">COLLECT</a>
    #     2 | Coins | <a href="https://gam.to/...">COLLECT</a>
    #
    # Strategy:
    #   1. Find ALL <a> tags where href contains "gam.to" and text is "COLLECT"
    #   2. For each such link, walk UP the DOM to find the nearest date heading
    #   3. Group links by date
    #   4. Emit one pending entry per active COLLECT link
    # ----------------------------------------------------------------

    date_pattern = re.compile(
        r"(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})",
        re.IGNORECASE
    )

    # Find ALL collect links on the page
    all_collect_links = [
        a for a in soup.find_all("a", href=True)
        if "gam.to" in a["href"] and a.get_text(strip=True).upper() == "COLLECT"
    ]

    print(f"  [8bpreward] found {len(all_collect_links)} total COLLECT link(s) on page")

    # For each collect link, find its associated date by walking up the DOM
    # and searching siblings/ancestors for a date-like text node.
    def find_date_for_link(link_tag) -> str | None:
        """Walk up the DOM from a COLLECT link to find the nearest date heading."""
        node = link_tag
        for _ in range(10):  # walk up max 10 levels
            node = node.parent
            if node is None:
                break
            # Search all text within this container for a date pattern
            # but stop if we find it to avoid going too far up
            text = node.get_text(" ", strip=True)
            m = date_pattern.search(text)
            if m:
                parsed = parse_date_ordinal(m.group(1))
                if parsed:
                    return parsed
        return None

    # Group collect links by date
    date_to_links: dict[str, list] = {}
    for link in all_collect_links:
        link_date = find_date_for_link(link) or today
        if link_date not in date_to_links:
            date_to_links[link_date] = []
        date_to_links[link_date].append(link)

    # Get set of dates already having a real code (from this scrape run)
    real_code_dates = {r["date"] for r in results if not r.get("pending")}

    pending_keys = set()
    for link_date, links in sorted(date_to_links.items(), reverse=True):
        print(f"  [8bpreward] {link_date}: {len(links)} active COLLECT link(s)")

        # If we already found a real code for this date, don't add pending
        if link_date in real_code_dates:
            print(f"  [8bpreward] skipping pending for {link_date} — real code already found")
            continue

        for i, link in enumerate(links):
            slot_num = len(links) - i  # number from bottom: newest = highest
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
    pending = [r for r in results if r.get("pending")]
    print(f"[8bpreward] total: {len(real)} real code(s), {len(pending)} pending slot(s)")
    return results


def extract_codes(raw: str, label: str) -> list:
    """
    Generic code extractor for taplink pages.
    Pattern: BONUS CODE: XXXX or CODE: XXXX
    """
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
    """Returns set of uppercased code strings already stored."""
    return {e["code"].upper() for e in entries}


def scrape_coinscrazy(game: str) -> list:
    src = SOURCES[game]
    try:
        resp = requests.get(src["url"], headers=HEADERS, timeout=25)
        resp.raise_for_status()
    except Exception as e:
        print(f"[{game}] coinscrazy fetch error: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    content = soup.find("article") or soup.find("div", class_=re.compile("entry|post|content")) or soup.body
    if not content:
        print(f"[{game}] no content container found")
        return []

    results = []
    current_date = None
    seen = set()

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

    print(f"[{game}] coinscrazy: found {len(results)} codes, sample: {[r['code'] for r in results[:3]]}")
    return results


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
    print(f"[gaminator-site] gaminator.com: found {len(results)} codes")
    return results


def scrape_taplink_gaminator() -> list:
    print("[taplink-gaminator] fetching...")
    raw = fetch_with_fallback(TAPLINK_GAMINATOR["url"])
    if not raw:
        print("[taplink-gaminator] could not fetch page")
        return []
    results = extract_codes(raw, TAPLINK_GAMINATOR["label"])
    print(f"[taplink-gaminator] found {len(results)} codes, sample: {[r['code'] for r in results[:5]]}")
    return results


def scrape_taplink_slotpark() -> list:
    print("[taplink-slotpark] fetching...")
    raw = fetch_with_fallback(TAPLINK_SLOTPARK["url"])
    if not raw:
        print("[taplink-slotpark] could not fetch page")
        return []
    results = extract_codes(raw, TAPLINK_SLOTPARK["label"])
    print(f"[taplink-slotpark] found {len(results)} codes, sample: {[r['code'] for r in results[:5]]}")
    return results


def merge(existing: list, new_entries: list) -> tuple:
    """
    Merge new entries into existing list.
    - For real codes: dedup by code string (uppercased).
    - For pending entries: dedup by the pending key (PENDING_date_slot),
      but also REMOVE a pending entry if a real code for the same date now exists.
      We don't remove pending entries that have no matching real code yet.
    """
    known_codes = existing_codes(existing)

    # Build set of dates that now have a real code
    real_dates = {e["date"] for e in existing if not e.get("pending")}
    for e in new_entries:
        if not e.get("pending"):
            real_dates.add(e.get("date"))

    added = 0
    now = datetime.now(timezone.utc).isoformat()

    for entry in new_entries:
        code_key = entry["code"].upper()

        if entry.get("pending"):
            # Skip pending if we already have a real code for this date
            if entry.get("date") in real_dates:
                print(f"  [merge] skipping pending {entry['code']} — real code exists for {entry['date']}")
                continue
            # Skip if this pending slot is already stored
            if code_key in known_codes:
                continue
        else:
            # Real code — skip duplicates
            if code_key in known_codes:
                continue

        existing.append({
            "code":        entry["code"],
            "date":        entry.get("date"),
            "source":      entry.get("source"),
            "pending":     entry.get("pending", False),
            "collect_url": entry.get("collect_url"),  # only for pending entries
            "found_at":    now
        })
        known_codes.add(code_key)
        added += 1

    # Clean up: remove any pending entries whose date now has a real code
    before = len(existing)
    existing = [
        e for e in existing
        if not (e.get("pending") and e.get("date") in real_dates)
    ]
    removed = before - len(existing)
    if removed:
        print(f"  [merge] removed {removed} pending entries superseded by real codes")

    return existing, added


def main():
    data = load_existing()
    total_added = 0

    # --- Gaminator ---
    gaminator_new = (
        scrape_coinscrazy("gaminator")
        + scrape_gaminator_site()
        + scrape_8bpreward()
        + scrape_taplink_gaminator()
    )
    data["gaminator"], added = merge(data["gaminator"], gaminator_new)
    total_added += added
    print(f"[gaminator] +{added} new codes (total: {len(data['gaminator'])})")

    # --- Slotpark ---
    slotpark_new = (
        scrape_coinscrazy("slotpark")
        + scrape_taplink_slotpark()
    )
    data["slotpark"], added = merge(data["slotpark"], slotpark_new)
    total_added += added
    print(f"[slotpark] +{added} new codes (total: {len(data['slotpark'])})")

    save(data)
    print(f"Done. Total new codes added: {total_added}")


if __name__ == "__main__":
    main()
