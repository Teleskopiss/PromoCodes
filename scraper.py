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

TAPLINK_SLOTPARK = {
    "url": "https://taplink.cc/slotpark",
    "label": "taplink.cc"
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}


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


def parse_date(text: str):
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
            parsed = parse_date(date_match.group(1))
            if parsed:
                current_date = parsed

        for m in re.finditer(r"CODE:-\s*([A-Za-z0-9]{2,12})", text, re.IGNORECASE):
            code = m.group(1).strip()
            if code.upper() not in seen:
                seen.add(code.upper())
                results.append({
                    "code": code,
                    "date": current_date,
                    "source": src["label"]
                })

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
            results.append({
                "code": code,
                "date": today,
                "source": GAMINATOR_SITE["label"]
            })

    print(f"[gaminator-site] gaminator.com: found {len(results)} codes")
    return results


def scrape_8bpreward() -> list:
    """Scrape 8bpreward.win for gaminator bonus codes.
    The page is a Blogger site — codes are embedded in the raw HTML source
    (inside JS data blobs / post body HTML), not in the rendered DOM text.
    We therefore search resp.text directly after HTML-unescaping.
    Pattern on page: BONUS CODE : ra1n
    """
    try:
        resp = requests.get(EIGHT_BP_REWARD["url"], headers=HEADERS, timeout=25)
        resp.raise_for_status()
    except Exception as e:
        print(f"[8bpreward] fetch error: {e}")
        return []

    # HTML-unescape so &#32; &amp; etc. resolve to plain text
    from html import unescape
    raw = unescape(resp.text)

    results = []
    seen = set()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Primary pattern: "BONUS CODE : ra1n" — flexible spacing, colon, dash, or equals
    # Code length 3-20 chars (covers short codes like "ra1n")
    for m in re.finditer(
        r"BONUS\s+CODE\s*[:\-=]\s*([A-Za-z0-9]{3,20})",
        raw, re.IGNORECASE
    ):
        code = m.group(1).strip()
        if code.upper() not in seen:
            seen.add(code.upper())
            results.append({
                "code": code,
                "date": today,
                "source": EIGHT_BP_REWARD["label"]
            })

    print(f"[8bpreward] found {len(results)} codes, sample: {[r['code'] for r in results[:5]]}")
    return results


def scrape_taplink_slotpark() -> list:
    """Scrape taplink.cc/slotpark for Slotpark bonus codes.
    Taplink pages embed their content in JSON inside <script> tags.
    We search the raw HTML for any CODE / BONUS CODE pattern.
    """
    try:
        resp = requests.get(TAPLINK_SLOTPARK["url"], headers=HEADERS, timeout=25)
        resp.raise_for_status()
    except Exception as e:
        print(f"[taplink-slotpark] fetch error: {e}")
        return []

    from html import unescape
    raw = unescape(resp.text)

    results = []
    seen = set()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Match: "BONUS CODE : XXXX", "CODE: XXXX", "code - XXXX"
    for m in re.finditer(
        r"(?:BONUS\s+)?CODE\s*[:\-=]\s*([A-Za-z0-9]{3,20})",
        raw, re.IGNORECASE
    ):
        code = m.group(1).strip()
        if code.upper() not in seen:
            seen.add(code.upper())
            results.append({
                "code": code,
                "date": today,
                "source": TAPLINK_SLOTPARK["label"]
            })

    print(f"[taplink-slotpark] found {len(results)} codes, sample: {[r['code'] for r in results[:5]]}")
    return results


def merge(existing: list, new_entries: list) -> tuple:
    known = existing_codes(existing)
    added = 0
    now = datetime.now(timezone.utc).isoformat()
    for entry in new_entries:
        if entry["code"].upper() not in known:
            existing.append({
                "code":     entry["code"],
                "date":     entry.get("date"),
                "source":   entry.get("source"),
                "found_at": now
            })
            known.add(entry["code"].upper())
            added += 1
    return existing, added


def main():
    data = load_existing()

    total_added = 0

    # Gaminator
    gaminator_new = scrape_coinscrazy("gaminator") + scrape_gaminator_site() + scrape_8bpreward()
    data["gaminator"], added = merge(data["gaminator"], gaminator_new)
    total_added += added
    print(f"[gaminator] +{added} new codes (total: {len(data['gaminator'])})")

    # Slotpark
    slotpark_new = scrape_coinscrazy("slotpark") + scrape_taplink_slotpark()
    data["slotpark"], added = merge(data["slotpark"], slotpark_new)
    total_added += added
    print(f"[slotpark] +{added} new codes (total: {len(data['slotpark'])})")

    save(data)
    print(f"Done. Total new codes added: {total_added}")


if __name__ == "__main__":
    main()
