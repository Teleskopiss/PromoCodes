import json
import re
import os
from datetime import datetime, timezone
from bs4 import BeautifulSoup
import requests

CODES_FILE = "codes.json"

SOURCES = {
    "gaminator": {
        "url": "https://coinscrazy.com/gaminator-free-coins/",
        "label": "coinscrazy.com",
        "prefix": "CODE:-"
    },
    "slotpark": {
        "url": "https://coinscrazy.com/slotpark-bonus-code-free-chips/",
        "label": "coinscrazy.com",
        "prefix": "CODE:-"
    }
}

GAMINATOR_SITE = {
    "url": "https://gaminator.com/en/promotions",
    "label": "gaminator.com",
    "prefix": "CODE:"
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}


def load_existing() -> dict:
    if os.path.exists(CODES_FILE):
        with open(CODES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"gaminator": [], "slotpark": []}


def save(data: dict):
    with open(CODES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def existing_codes(entries: list) -> set:
    return {e["code"] for e in entries}


def parse_date(text: str) -> str | None:
    """
    Try to parse a date string like '05 January 2026' or '31 December 2025'.
    Returns ISO date string 'YYYY-MM-DD' or None.
    """
    text = text.strip()
    for fmt in ("%d %B %Y", "%B %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def scrape_coinscrazy(game: str) -> list:
    """
    Scrape coinscrazy page for CODE:- entries.
    Each code block is preceded by a date line.
    Returns list of {code, date, source} dicts.
    """
    src = SOURCES[game]
    try:
        resp = requests.get(src["url"], headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"[{game}] coinscrazy fetch error: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    # Gather all bold/strong text elements in document order
    results = []
    current_date = None

    # Walk every <strong> or <b> tag in the article body
    content = soup.find("article") or soup.find("main") or soup.body
    if not content:
        return []

    for tag in content.find_all(["strong", "b", "p"]):
        text = tag.get_text(" ", strip=True)

        # Check if it looks like a date line e.g. "Updated On: 05 January 2026" or "04 January 2026"
        date_match = re.search(
            r"(?:Updated On:\s*)?(\d{1,2}\s+[A-Za-z]+\s+\d{4})", text
        )
        if date_match:
            parsed = parse_date(date_match.group(1))
            if parsed:
                current_date = parsed
            continue

        # Check if it's a code line
        code_match = re.match(r"CODE:-\s*([A-Za-z0-9]+)", text, re.IGNORECASE)
        if code_match:
            code = code_match.group(1).strip()
            results.append({
                "code": code,
                "date": current_date,
                "source": src["label"]
            })

    print(f"[{game}] coinscrazy: found {len(results)} codes")
    return results


def scrape_gaminator_site() -> list:
    """
    Scrape gaminator.com promotions page for CODE: [code] entries.
    No date available — use today's date as found_at.
    """
    try:
        resp = requests.get(GAMINATOR_SITE["url"], headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"[gaminator-site] fetch error: {e}")
        return []

    text = resp.text
    codes = re.findall(r"CODE:\s*([A-Za-z0-9]+)", text, re.IGNORECASE)
    results = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for code in set(codes):
        results.append({
            "code": code.strip(),
            "date": today,
            "source": GAMINATOR_SITE["label"]
        })

    print(f"[gaminator-site] gaminator.com: found {len(results)} codes")
    return results


def merge(existing: list, new_entries: list) -> tuple[list, int]:
    """
    Merge new entries into existing list, deduplicating by code.
    Returns (merged_list, added_count).
    """
    known = existing_codes(existing)
    added = 0
    now = datetime.now(timezone.utc).isoformat()
    for entry in new_entries:
        if entry["code"] not in known:
            existing.append({
                "code": entry["code"],
                "date": entry.get("date"),
                "source": entry.get("source"),
                "found_at": now
            })
            known.add(entry["code"])
            added += 1
    return existing, added


def main():
    data = load_existing()
    if "gaminator" not in data:
        data["gaminator"] = []
    if "slotpark" not in data:
        data["slotpark"] = []

    total_added = 0

    # --- Gaminator: coinscrazy + gaminator.com ---
    gaminator_new = scrape_coinscrazy("gaminator") + scrape_gaminator_site()
    data["gaminator"], added = merge(data["gaminator"], gaminator_new)
    total_added += added
    print(f"[gaminator] +{added} new codes")

    # --- Slotpark: coinscrazy ---
    slotpark_new = scrape_coinscrazy("slotpark")
    data["slotpark"], added = merge(data["slotpark"], slotpark_new)
    total_added += added
    print(f"[slotpark] +{added} new codes")

    save(data)
    print(f"Done. Total new codes added: {total_added}")


if __name__ == "__main__":
    main()
