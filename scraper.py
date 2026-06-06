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


def scrape_8bpreward() -> list:
    """
    Scrapes https://www.8bpreward.win/2025/11/gaminator-codes.html

    Two things to find:

    1. ACTUAL CODES — only from the exact pattern:
           BONUS CODE : <code>
       This is displayed in a styled widget box at the top.
       We deliberately ignore 'CODE:' or 'bonus:' that appear elsewhere
       in the page description text (those are explanatory, not real codes).

    2. PENDING CODES — the page updates with COLLECT button links
       (gam.to/... URLs) grouped by date section, before other sites
       publish the actual code text. For each date section, we count
       how many active COLLECT links exist (not 'Expired'). We emit
       one 'pending' entry per COLLECT link found for that date.
       These are stored with pending=True so the UI can show
       "Pending code" placeholders.
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
    # 1. Find the BONUS CODE widget
    #    The widget contains text like: "BONUS CODE : ra1n"
    #    We search the entire soup text but ONLY for this exact pattern:
    #      BONUS CODE : <alphanum>
    #    We do NOT match bare 'CODE:' or 'bonus:' to avoid false positives.
    # ----------------------------------------------------------------
    full_text = unescape(soup.get_text(" ", strip=True))

    # Strict pattern: "BONUS CODE" followed by optional whitespace, colon,
    # optional whitespace, then the code (alphanumeric, 3-20 chars).
    # The space between BONUS and CODE is mandatory.
    for m in re.finditer(
        r"BONUS\s+CODE\s*:\s*([A-Za-z0-9]{3,20})",
        full_text
    ):
        code = m.group(1).strip()
        if code.upper() not in seen_codes:
            seen_codes.add(code.upper())
            print(f"  [8bpreward] found BONUS CODE: {code}")
            results.append({
                "code": code,
                "date": today,
                "source": EIGHT_BP_REWARD["label"],
                "pending": False
            })

    # ----------------------------------------------------------------
    # 2. Parse date sections and count COLLECT buttons
    #
    #    Page structure (Blogger):
    #      <div> or <table> containing date heading e.g. "6th June 2026"
    #      followed by table rows with:
    #        #  | GIFT  | LINK
    #        1  | Coins | <a href="https://gam.to/...">COLLECT</a>
    #        2  | Coins | <a href="https://gam.to/...">COLLECT</a>
    #
    #    Strategy: find all text nodes matching a date pattern,
    #    then for each date find the following table rows with gam.to links.
    # ----------------------------------------------------------------

    # Find all elements whose text looks like a date heading
    # e.g. "6th June 2026", "21st January 2026"
    date_pattern = re.compile(
        r"^\s*(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})\s*$",
        re.IGNORECASE
    )

    # Collect all tags that are date headings
    date_tags = []
    for tag in soup.find_all(True):
        txt = tag.get_text(" ", strip=True)
        if date_pattern.match(txt) and len(txt) < 40:
            # Avoid matching parent containers that contain more than just the date
            if len(list(tag.children)) <= 3:
                date_tags.append(tag)

    # Deduplicate: keep only innermost tags per date string
    seen_date_tags = {}
    for tag in date_tags:
        txt = tag.get_text(" ", strip=True)
        # prefer the tag with fewest descendants (most specific)
        existing = seen_date_tags.get(txt)
        if existing is None or len(list(tag.descendants)) < len(list(existing.descendants)):
            seen_date_tags[txt] = tag
    date_tags = list(seen_date_tags.values())

    print(f"  [8bpreward] found {len(date_tags)} date sections: {[t.get_text(strip=True) for t in date_tags[:5]]}")

    # Track which (date, slot_number) pending entries already exist
    # We use a composite key: date + slot index to avoid duplication on re-runs
    pending_keys = set()

    for date_tag in date_tags:
        raw_date_str = date_tag.get_text(" ", strip=True)
        parsed_date = parse_date_ordinal(raw_date_str) or today

        # Find the parent container that holds both the date and the table
        # Walk up until we find a tag that contains <a href="gam.to"> links
        container = date_tag
        collect_links = []
        for _ in range(6):  # walk up max 6 levels
            container = container.parent
            if container is None:
                break
            collect_links = [
                a for a in container.find_all("a", href=True)
                if "gam.to" in a["href"] and a.get_text(strip=True).upper() == "COLLECT"
            ]
            if collect_links:
                break

        if not collect_links:
            print(f"  [8bpreward] {raw_date_str}: no COLLECT links found")
            continue

        print(f"  [8bpreward] {raw_date_str} ({parsed_date}): {len(collect_links)} COLLECT link(s)")

        for i, link in enumerate(collect_links):
            pending_key = f"{parsed_date}_{i}"
            if pending_key not in pending_keys:
                pending_keys.add(pending_key)
                slot_num = len(collect_links) - i  # highest number = newest
                results.append({
                    "code": f"PENDING_{parsed_date}_{slot_num}",  # unique stable ID
                    "date": parsed_date,
                    "source": EIGHT_BP_REWARD["label"],
                    "pending": True,
                    "collect_url": link["href"]  # the actual gam.to link
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


def parse_date_plain(text: str) -> str | None:
    text = text.strip()
    for fmt in ("%d %B %Y", "%B %d, %Y", "%d %b %Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


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
