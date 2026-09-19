import json
import re
import time
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ============================================================
# CONFIG
# ============================================================

COE_URL = "https://coe.annauniv.edu/home/index.php"
FALLBACK_URL = "https://aucoe.annauniv.edu/"

OUTPUT = Path("data/notifications.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}


# ============================================================
# DATE PATTERNS
# ============================================================

DATE_TIME_RE = re.compile(
    r"\b"
    r"\d{1,2}\s+"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
    r"\s+\d{4}"
    r"\s+\d{1,2}:\d{2}\s*(?:AM|PM)"
    r"\b",
    re.IGNORECASE,
)

DATE_RE = re.compile(
    r"\b(?:"
    r"\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}"
    r"|"
    r"\d{1,2}\s+"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
    r"\s+\d{4}"
    r")\b",
    re.IGNORECASE,
)


# ============================================================
# BASIC HELPERS
# ============================================================

def clean(text):
    return " ".join(str(text or "").split())


def fetch(url):
    for attempt in range(1, 4):
        try:
            print(f"Fetching {url} (attempt {attempt}/3)")

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=45,
                verify=False,
                params={"_": str(int(time.time()))},
            )

            response.raise_for_status()

            print(f"Fetched successfully: {len(response.text)} bytes")

            return response.text

        except Exception as error:
            print(f"Fetch error: {error}")

            if attempt < 3:
                time.sleep(2)

    return None


def parse_date(value):
    value = clean(value)

    formats = [
        "%d %b %Y %I:%M %p",
        "%d %B %Y %I:%M %p",
        "%d %b %Y",
        "%d %B %Y",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%d.%m.%Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass

    return datetime.min


def extract_posted_date(text):
    text = clean(text)

    # IMPORTANT:
    # Posting timestamp always wins over deadline dates.
    match = DATE_TIME_RE.search(text)

    if match:
        return match.group(0)

    match = DATE_RE.search(text)

    if match:
        return match.group(0)

    return ""


def is_useful_link(href, text):
    href_lower = href.lower()
    text_lower = text.lower()

    document = re.search(
        r"\.(pdf|doc|docx|xls|xlsx)(?:[?#].*)?$",
        href_lower,
    )

    words = (
        "click here",
        "notification",
        "revaluation",
        "examination",
        "answer script",
        "answer scripts",
        "result",
        "timetable",
        "valuation",
        "application",
        "circular",
        "web portal",
        "kind attention",
    )

    return bool(document) or any(word in text_lower for word in words)


# ============================================================
# PARSER
# ============================================================

def parse_page(html, source):
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()

    page_text = clean(soup.get_text(" ", strip=True))

    timestamps = DATE_TIME_RE.findall(page_text)

    print(f"Posting timestamps found on page: {len(timestamps)}")

    for timestamp in timestamps[:20]:
        print(f"  TIMESTAMP: {timestamp}")

    links = soup.find_all("a", href=True)

    print(f"Total links found: {len(links)}")

    candidates = []

    for link in links:
        href = clean(link.get("href", ""))
        text = clean(link.get_text(" ", strip=True))

        if not href:
            continue

        if href.startswith("#"):
            continue

        if href.lower().startswith(
            ("javascript:", "mailto:", "tel:")
        ):
            continue

        absolute_url = urljoin(source, href)

        if is_useful_link(absolute_url, text):
            candidates.append(
                (link, absolute_url, text)
            )

    print(f"Notification-like links found: {len(candidates)}")

    results = {}

    # --------------------------------------------------------
    # Find records
    # --------------------------------------------------------

    for link, url, link_text in candidates:

        # Gather nearby text from parents.
        contexts = []

        current = link

        for _ in range(7):
            current = current.parent

            if current is None:
                break

            text = clean(
                current.get_text(" ", strip=True)
            )

            if text and text not in contexts:
                contexts.append(text)

        # Use the smallest nearby context that contains
        # useful notification wording.
        context = ""

        for item in contexts:
            lower = item.lower()

            if (
                "notification" in lower
                or "revaluation" in lower
                or "examination" in lower
                or "answer script" in lower
                or "web portal" in lower
                or "kind attention" in lower
                or "result" in lower
            ):
                context = item
                break

        if not context and contexts:
            context = contexts[0]

        # ----------------------------------------------------
        # Extract posting date
        # ----------------------------------------------------

        posted_date = extract_posted_date(context)

        # Search nearby HTML if parent context did not contain
        # the timestamp.
        if not posted_date:

            previous_texts = link.find_all_previous(
                string=True,
                limit=30,
            )

            for node in previous_texts:
                candidate = extract_posted_date(str(node))

                if candidate:
                    posted_date = candidate
                    break

        if not posted_date:

            next_texts = link.find_all_next(
                string=True,
                limit=30,
            )

            for node in next_texts:
                candidate = extract_posted_date(str(node))

                if candidate:
                    posted_date = candidate
                    break

        # Last resort: use page date.
        if not posted_date:
            posted_date = extract_posted_date(page_text)

        if not posted_date:
            continue

        # ----------------------------------------------------
        # Title
        # ----------------------------------------------------

        title = link_text

        if not title or title.lower() in {
            "click here",
            "here",
            "read more",
            "download",
        }:
            title = context

        title = clean(title)

        # Remove timestamp from beginning of title.
        title = re.sub(
            r"^\s*" + re.escape(posted_date) + r"\s*",
            "",
            title,
            flags=re.IGNORECASE,
        )

        title = re.sub(
            r"\s*click\s*here\s*$",
            "",
            title,
            flags=re.IGNORECASE,
        )

        title = clean(title)

        if not title:
            title = "Anna University COE Notification"

        if len(title) > 500:
            title = title[:497] + "..."

        # ----------------------------------------------------
        # Description
        # ----------------------------------------------------

        description = clean(context)

        if len(description) > 1000:
            description = description[:997] + "..."

        record = {
            "title": title,
            "url": url,
            "date": posted_date,
            "description": description,
            "source": source,
        }

        results[url] = record

        print()
        print("FOUND NOTIFICATION")
        print(f"Date: {posted_date}")
        print(f"Title: {title[:180]}")
        print(f"URL: {url}")

    print()
    print(f"Notifications parsed: {len(results)}")

    return list(results.values())


# ============================================================
# EXISTING DATA
# ============================================================

def load_existing():
    if not OUTPUT.exists():
        return []

    try:
        with open(OUTPUT, "r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, list):
            return data

    except Exception as error:
        print(f"JSON read error: {error}")

    return []


# ============================================================
# MERGE
# ============================================================

def merge(old_items, new_items):
    combined = {}

    for item in old_items:
        url = clean(item.get("url", ""))

        if url:
            combined[url] = item

    for item in new_items:
        url = clean(item.get("url", ""))

        if url:
            combined[url] = item

    items = list(combined.values())

    items.sort(
        key=lambda item: parse_date(
            item.get("date", "")
        ),
        reverse=True,
    )

    return items[:200]


# ============================================================
# SAVE
# ============================================================

def save(items):
    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with open(
        OUTPUT,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            items,
            file,
            ensure_ascii=False,
            indent=2,
        )

        file.write("\n")


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 65)
    print("ANNA UNIVERSITY COE MONITOR")
    print("=" * 65)

    old_items = load_existing()

    print(f"Existing records: {len(old_items)}")

    # --------------------------------------------------------
    # PRIMARY WEBSITE
    # --------------------------------------------------------

    html = fetch(COE_URL)

    new_items = []

    if html:
        new_items = parse_page(
            html,
            COE_URL,
        )

    # --------------------------------------------------------
    # FALLBACK
    # --------------------------------------------------------

    if not new_items:

        print()
        print("Primary parser found nothing.")
        print("Trying fallback...")

        fallback_html = fetch(FALLBACK_URL)

        if fallback_html:
            new_items = parse_page(
                fallback_html,
                FALLBACK_URL,
            )

    # --------------------------------------------------------
    # NOTHING PARSED
    # --------------------------------------------------------

    if not new_items:

        print()
        print("=" * 65)
        print("NO NEW DATA COULD BE PARSED.")
        print(
            f"Keeping existing {len(old_items)} records."
        )
        print("=" * 65)

        return

    # --------------------------------------------------------
    # MERGE
    # --------------------------------------------------------

    old_urls = {
        clean(item.get("url", ""))
        for item in old_items
    }

    merged_items = merge(
        old_items,
        new_items,
    )

    genuinely_new = [
        item
        for item in new_items
        if clean(item.get("url", "")) not in old_urls
    ]

    save(merged_items)

    # --------------------------------------------------------
    # RESULT
    # --------------------------------------------------------

    print()
    print("=" * 65)
    print("RESULT")
    print("=" * 65)

    print(f"Old records:    {len(old_items)}")
    print(f"Parsed records: {len(new_items)}")
    print(f"Total records:  {len(merged_items)}")
    print(f"Genuinely new:  {len(genuinely_new)}")

    print()
    print("LATEST RECORDS")
    print("-" * 65)

    for item in merged_items[:10]:

        print(
            f"{item.get('date', '')} | "
            f"{item.get('title', '')[:150]}"
        )

        print(
            f"URL: {item.get('url', '')}"
        )

        print("-" * 65)

    print("CHECK COMPLETE")
    print("=" * 65)


if __name__ == "__main__":
    main()
