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
# When a new code is found from these, the old one is replaced (not kept as extra).
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
}


def extract_bonus_code_from_game_announce(soup: BeautifulSoup) -> str | None:
    """
    The active bonus code lives inside <div class="game-announce"> as plain text:
        BONUS CODE: ra1n

    This is the canonical location on 8bpreward.win — always check here first.
    The 'NEW BONUS CODE' button is a separate element in the same container;
    we ignore it and only read the text of game-announce itself.
    """
    announce = soup.find("div", class_="game-announce")
    if announce:
        text = announce.get_text(" ", strip=True)
        print(f"  [8bpreward] game-announce text: {text!r}")
        m = re.search(r"BONUS\s+CODE\s*[:\-]\s*([A-Za-z0-9]{2,15})", text, re.IGNORECASE)
        if m:
            code = m.group(1).strip()
            if code.upper() not in _BLACKLIST:
                print(f"  [8bpreward] game-announce code: {code}")
                return code
        else:
            print(f"  [8bpreward] game-announce found but no BONUS CODE pattern in: {text!r}")
    else:
        print("  [8bpreward] div.game-announce NOT found — checking fallback divs")

    # Fallback: any div with class containing 'announce' or 'bonus'
    for div in soup.find_all("div", class_=re.compile(r"announce|bonus", re.IGNORECASE)):
        text = div.get_text(" ", strip=True)
        m = re.search(r"BONUS\s+CODE\s*[:\-]\s*([A-Za-z0-9]{2,15})", text, re.IGNORECASE)
        if m:
            code = m.group(1).strip()
            if code.upper() not in _BLACKLIST:
                print(f"  [8bpreward] fallback div code: {code}")
                return code

    return None


def scrape_8bpreward() -> list:
    """
    Scrapes https://www.8bpreward.win/2025/11/gaminator-codes.html

    1. ACTUAL CODE — from <div class="game-announce"> which contains:
           BONUS CODE: ra1n
       This is where the site always stores the current active bonus code.
       Only ONE code at a time from this source.

    2. PENDING entries — one per active COLLECT link (gam.to URLs).
    """
    print("[8bpreward] fetching...")
    html = fetch_html(EIGHT_BP_REWARD["url"])
    if not html:
        print("[8bpreward] could not fetch page")
        return []

    soup = BeautifulSoup(html, "html.parser")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    results = []

    # --- 1. Extract the active bonus code from div.game-announce ---
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
    return results


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

    print(f"[{game}] coinscrazy: {len(results)} codes, sample: {[r['code'] for r in results[:3]]}")
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
    print(f"[gaminator-site] {len(results)} codes")
    return results


def scrape_taplink_gaminator() -> list:
    print("[taplink-gaminator] fetching...")
    raw = fetch_with_fallback(TAPLINK_GAMINATOR["url"])
    if not raw:
        print("[taplink-gaminator] could not fetch page")
        return []
    # Only return the FIRST code found — taplink has one active code at a time
    all_codes = extract_codes(raw, TAPLINK_GAMINATOR["label"])
    result = all_codes[:1]
    print(f"[taplink-gaminator] active code: {[r['code'] for r in result]}")
    return result


def scrape_taplink_slotpark() -> list:
    print("[taplink-slotpark] fetching...")
    raw = fetch_with_fallback(TAPLINK_SLOTPARK["url"])
    if not raw:
        print("[taplink-slotpark] could not fetch page")
        return []
    # Only return the FIRST code found — taplink has one active code at a time
    all_codes = extract_codes(raw, TAPLINK_SLOTPARK["label"])
    result = all_codes[:1]
    print(f"[taplink-slotpark] active code: {[r['code'] for r in result]}")
    return result


def merge(existing: list, new_entries: list) -> tuple:
    """
    Merges new scraped entries into the existing list.

    Rules:
    - SINGLE_CODE_SOURCES (taplink, 8bpreward): only ONE active code per source.
      When a new code arrives from such a source, the old one is marked
      expired (replaced=True) rather than kept active.
    - coinscrazy.com: multiple codes allowed; each expires after 24h (handled frontend).
    - Pending entries are removed when a real code exists for the same date.
    """
    now = datetime.now(timezone.utc).isoformat()
    added = 0

    # Build lookup: source -> current active code entry (for single-code sources)
    active_by_source: dict[str, dict] = {}
    for entry in existing:
        src = entry.get("source", "")
        if src in SINGLE_CODE_SOURCES and not entry.get("pending") and not entry.get("replaced"):
            active_by_source[src] = entry

    known_codes = existing_codes(existing)

    real_dates = {e["date"] for e in existing if not e.get("pending")}
    for e in new_entries:
        if not e.get("pending"):
            real_dates.add(e.get("date"))

    for entry in new_entries:
        code_key = entry["code"].upper()
        src = entry.get("source", "")

        if entry.get("pending"):
            if entry.get("date") in real_dates:
                continue
            if code_key in known_codes:
                continue
        else:
            if code_key in known_codes:
                # Already known — just update found_at to refresh it
                continue

            # For single-code sources: mark old code as replaced
            if src in SINGLE_CODE_SOURCES and src in active_by_source:
                old = active_by_source[src]
                if old["code"].upper() != code_key:
                    print(f"  [merge] {src}: replacing '{old['code']}' with '{entry['code']}'")
                    old["replaced"] = True
                    old["replaced_at"] = now
                    del active_by_source[src]

        existing.append({
            "code":        entry["code"],
            "date":        entry.get("date"),
            "source":      entry.get("source"),
            "pending":     entry.get("pending", False),
            "collect_url": entry.get("collect_url"),
            "found_at":    now
        })
        known_codes.add(code_key)
        if src in SINGLE_CODE_SOURCES and not entry.get("pending"):
            active_by_source[src] = existing[-1]
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


def main():
    data = load_existing()
    total_added = 0

    gaminator_new = (
        scrape_coinscrazy("gaminator")
        + scrape_gaminator_site()
        + scrape_8bpreward()
        + scrape_taplink_gaminator()
    )
    data["gaminator"], added = merge(data["gaminator"], gaminator_new)
    total_added += added
    print(f"[gaminator] +{added} (total: {len(data['gaminator'])})")

    slotpark_new = (
        scrape_coinscrazy("slotpark")
        + scrape_taplink_slotpark()
    )
    data["slotpark"], added = merge(data["slotpark"], slotpark_new)
    total_added += added
    print(f"[slotpark] +{added} (total: {len(data['slotpark'])})")

    save(data)
    print(f"Done. Total added: {total_added}")


if __name__ == "__main__":
    main()
