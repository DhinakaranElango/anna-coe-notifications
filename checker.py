import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "data" / "notifications.json"

PRIMARY = "https://coe.annauniv.edu/home/index.php"
FALLBACK = "https://aucoe.annauniv.edu/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10) "
        "AppleWebKit/537.36 Chrome/140.0 Mobile Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

KEYWORDS = re.compile(
    r"(notification|circular|timetable|time table|exam|result|"
    r"hall.?ticket|revaluation|semester|schedule|fee|registration|"
    r"application|important|examination|arrear|regulation|"
    r"certificate|convocation|marksheet|academic|assessment|"
    r"candidate|photograph|answer scripts)",
    re.I,
)

session = requests.Session()
session.headers.update(HEADERS)


def fetch(url):
    """Download a page with retries."""
    for attempt in range(3):
        try:
            print(
                f"Trying {url} "
                f"(attempt {attempt + 1}/3)"
            )

            response = session.get(
                url,
                timeout=30,
                verify=False,
            )

            response.raise_for_status()

            if len(response.text) < 500:
                raise RuntimeError(
                    "Page response is unusually small"
                )

            print(f"Successfully fetched {url}")
            return response.text

        except Exception as error:
            print(f"Fetch failed: {error}")

            if attempt < 2:
                time.sleep(2 ** attempt)

    return None


def clean(text):
    return " ".join(text.split())


def extract_date(text):
    """Extract the notification date."""
    patterns = [
        r"\b\d{1,2}[-/\.]\d{1,2}[-/\.]\d{2,4}\b",

        r"\b\d{1,2}\s+"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"[a-z]*\s+\d{4}\b",

        r"\b\d{1,2}\s+"
        r"(?:January|February|March|April|May|June|July|"
        r"August|September|October|November|December)"
        r"\s+\d{4}\b",

        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"[a-z]*\s+\d{1,2},?\s+\d{4}\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.I)

        if match:
            return match.group(0)

    return ""


def extract_datetime(text):
    """
    Extract date + time when the COE page provides it.
    Example:
    19 Sep 2026 03:13 PM
    """
    pattern = re.compile(
        r"\b(\d{1,2}\s+"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"[a-z]*\s+\d{4})"
        r"(?:\s+(\d{1,2}:\d{2}\s*(?:AM|PM)))?",
        re.I,
    )

    match = pattern.search(text)

    if not match:
        return ""

    date_part = match.group(1)
    time_part = match.group(2) or ""

    return clean(f"{date_part} {time_part}")


def find_notification_blocks(soup):
    """
    Find likely COE notification/message blocks.

    The COE website has historically used tables/divs containing:
    - date/time
    - notification message
    - Click Here link
    """

    blocks = []

    selectors = [
        "tr",
        "li",
        ".message",
        ".messages",
        ".notice",
        ".notification",
        ".panel",
        "p",
        "div",
    ]

    seen = set()

    for selector in selectors:
        for element in soup.select(selector):
            text = clean(
                element.get_text(" ", strip=True)
            )

            if not text:
                continue

            # A COE institution message normally has
            # a date/time and meaningful notification text.
            has_date = bool(
                re.search(
                    r"\b\d{1,2}\s+"
                    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
                    r"[a-z]*\s+\d{4}\b",
                    text,
                    re.I,
                )
            )

            has_keyword = bool(KEYWORDS.search(text))

            if not has_date and not has_keyword:
                continue

            key = text[:1000]

            if key in seen:
                continue

            seen.add(key)
            blocks.append(element)

    return blocks


def parse_page(html, source):
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(
        ["script", "style", "noscript"]
    ):
        tag.decompose()

    items = []
    seen_urls = set()

    # ---------------------------------------------------------
    # Method 1:
    # Find notification blocks and their Click Here links.
    # ---------------------------------------------------------

    blocks = find_notification_blocks(soup)

    for block in blocks:
        block_text = clean(
            block.get_text(" ", strip=True)
        )

        date = extract_date(block_text)
        datetime_text = extract_datetime(block_text)

        links = block.find_all(
            "a",
            href=True
        )

        for link in links:
            href = link.get("href", "").strip()
            link_text = clean(
                link.get_text(" ", strip=True)
            )

            if not href:
                continue

            if href.startswith("#"):
                continue

            if href.lower().startswith(
                ("javascript:", "mailto:", "tel:")
            ):
                continue

            url = urljoin(source, href)

            lower_url = url.lower()

            # Only accept useful document/portal links.
            is_document = bool(
                re.search(
                    r"\.(pdf|doc|docx|xls|xlsx)"
                    r"(?:[?#].*)?$",
                    lower_url,
                    re.I,
                )
            )

            is_click_link = (
                link_text.lower()
                in {"click here", "clickhere"}
            )

            if not is_document and not is_click_link:
                continue

            if url in seen_urls:
                continue

            seen_urls.add(url)

            # -------------------------------------------------
            # Build a useful title from the notification text.
            # -------------------------------------------------

            title = block_text

            # Remove the date/time at the beginning.
            title = re.sub(
                r"^\s*\d{1,2}\s+"
                r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
                r"[a-z]*\s+\d{4}"
                r"(?:\s+\d{1,2}:\d{2}\s*(?:AM|PM))?"
                r"\s*",
                "",
                title,
                flags=re.I,
            )

            # Remove duplicated "Click Here".
            title = re.sub(
                r"\s*click\s*here\s*$",
                "",
                title,
                flags=re.I,
            )

            title = clean(title)

            if not title:
                title = "Anna University COE Notification"

            # Limit title length.
            if len(title) > 300:
                title = title[:297] + "..."

            description = block_text

            if len(description) > 800:
                description = (
                    description[:797] + "..."
                )

            items.append(
                {
                    "title": title,
                    "url": url,
                    "date": (
                        datetime_text
                        or date
                    ),
                    "description": description,
                    "source": source,
                }
            )

    # ---------------------------------------------------------
    # Method 2:
    # Generic link fallback.
    #
    # This catches document links that are not inside a
    # notification block.
    # ---------------------------------------------------------

    for link in soup.find_all(
        "a",
        href=True
    ):
        href = link.get("href", "").strip()

        if not href:
            continue

        if href.startswith("#"):
            continue

        if href.lower().startswith(
            ("javascript:", "mailto:", "tel:")
        ):
            continue

        url = urljoin(source, href)

        if url in seen_urls:
            continue

        is_document = bool(
            re.search(
                r"\.(pdf|doc|docx|xls|xlsx)"
                r"(?:[?#].*)?$",
                url,
                re.I,
            )
        )

        if not is_document:
            continue

        link_text = clean(
            link.get_text(" ", strip=True)
        )

        parent = (
            link.find_parent("tr")
            or link.find_parent("li")
            or link.find_parent("p")
            or link.find_parent("div")
        )

        context = ""

        if parent:
            context = clean(
                parent.get_text(
                    " ",
                    strip=True
                )
            )

        searchable = clean(
            f"{link_text} {context} {url}"
        )

        if not KEYWORDS.search(searchable):
            continue

        seen_urls.add(url)

        date = extract_date(context)
        datetime_text = extract_datetime(
            context
        )

        title = (
            context
            or link_text
            or "Anna University COE Notification"
        )

        if len(title) > 300:
            title = title[:297] + "..."

        items.append(
            {
                "title": title,
                "url": url,
                "date": (
                    datetime_text
                    or date
                ),
                "description": context[:800],
                "source": source,
            }
        )

    return items


def load_old():
    if not OUTPUT.exists():
        return []

    try:
        data = json.loads(
            OUTPUT.read_text(
                encoding="utf-8"
            )
        )

        if isinstance(data, list):
            return data

    except Exception as error:
        print(
            f"Could not read old notifications: "
            f"{error}"
        )

    return []


def date_sort_key(item):
    date_text = item.get(
        "date",
        ""
    )

    formats = [
        "%d %b %Y %I:%M %p",
        "%d %b %Y",
        "%d %B %Y %I:%M %p",
        "%d %B %Y",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%d.%m.%Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(
                date_text,
                fmt
            )
        except ValueError:
            pass

    # Try to find the date inside a larger string.
    match = re.search(
        r"(\d{1,2}\s+"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"[a-z]*\s+\d{4}"
        r"(?:\s+\d{1,2}:\d{2}\s*(?:AM|PM))?)",
        date_text,
        re.I,
    )

    if match:
        value = clean(match.group(1))

        for fmt in formats:
            try:
                return datetime.strptime(
                    value,
                    fmt
                )
            except ValueError:
                pass

    return datetime.min


# =============================================================
# MAIN
# =============================================================

old_items = load_old()

print("=" * 60)
print("ANNA UNIVERSITY COE CHECK")
print("=" * 60)

html = fetch(PRIMARY)

used_source = PRIMARY
items = []

if html:
    items = parse_page(
        html,
        PRIMARY
    )

print("DEBUG: Total HTML length:", len(html))

debug_soup = BeautifulSoup(html, "html.parser")

print("DEBUG: All visible page text:")
print(
    debug_soup.get_text(
        " ",
        strip=True
    )[:10000]
)

print("DEBUG: All links:")

for debug_link in debug_soup.find_all(
    "a",
    href=True
):
    print(
        "LINK:",
        debug_link.get_text(
            " ",
            strip=True
        ),
        "=>",
        debug_link.get("href")
    )

if not items:
    print(
        "Primary COE page produced no "
        "notifications."
    )

    print(
        "Trying official fallback..."
    )

    fallback_html = fetch(FALLBACK)

    if fallback_html:
        fallback_items = parse_page(
            fallback_html,
            FALLBACK
        )

        if fallback_items:
            items = fallback_items
            used_source = FALLBACK


# =============================================================
# SAFETY:
# Never erase good old data when the COE site fails.
# =============================================================

if not items:
    print(
        "WARNING: No notifications detected."
    )

    if old_items:
        print(
            f"Keeping existing "
            f"{len(old_items)} notifications."
        )

    else:
        print(
            "No previous notifications exist."
        )

    raise SystemExit(0)


# =============================================================
# Remove duplicates.
# =============================================================

unique = {}

for item in items:
    key = (
        item.get(
            "url",
            ""
        ).strip()
    )

    if not key:
        key = (
            item.get(
                "title",
                ""
            ).strip().lower()
        )

    unique[key] = item


items = list(
    unique.values()
)


# =============================================================
# Sort newest notification first.
# =============================================================

items.sort(
    key=date_sort_key,
    reverse=True
)


# =============================================================
# Keep maximum 200 notifications.
# =============================================================

items = items[:200]


# =============================================================
# Detect new notifications.
# =============================================================

old_keys = {
    (
        item.get(
            "title",
            ""
        ).strip().lower(),

        item.get(
            "url",
            ""
        ).strip(),
    )

    for item in old_items
}


new_items = [
    item
    for item in items

    if (
        item.get(
            "title",
            ""
        ).strip().lower(),

        item.get(
            "url",
            ""
        ).strip(),
    )
    not in old_keys
]


# =============================================================
# Save JSON.
# =============================================================

OUTPUT.parent.mkdir(
    parents=True,
    exist_ok=True
)

OUTPUT.write_text(
    json.dumps(
        items,
        ensure_ascii=False,
        indent=2
    ),
    encoding="utf-8"
)


# =============================================================
# Report.
# =============================================================

checked_at = datetime.now(
    timezone.utc
).isoformat()

print()
print(
    f"Source: {used_source}"
)

print(
    f"Notifications found: "
    f"{len(items)}"
)

print(
    f"New notifications: "
    f"{len(new_items)}"
)

print(
    f"Checked at: "
    f"{checked_at}"
)

print("=" * 60)

for item in new_items[:20]:
    print(
        "NEW:",
        item.get("date", ""),
        "|",
        item.get("title", ""),
        "->",
        item.get("url", "")
                                )
