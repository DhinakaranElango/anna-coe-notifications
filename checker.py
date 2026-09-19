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

# Official Anna University COE Institution Messages page
PRIMARY = "https://coe.annauniv.edu/home/index.php"
FALLBACK = "https://aucoe.annauniv.edu/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0 Mobile Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

KEYWORDS = re.compile(
    r"(notification|circular|timetable|time table|exam|result|"
    r"hall.?ticket|revaluation|semester|schedule|fee|registration|"
    r"application|important|examination|arrear|regulation|"
    r"certificate|convocation|marksheet|assessment|answer script|"
    r"institutions)",
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
                timeout=45,
                verify=False,
                params={"_": int(time.time())},
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


def extract_posted_date(element):
    """
    Extract the COE notification POSTED date.

    Important:
    Dates inside the notification message (for example,
    an extended deadline such as 21-09-2026) must NOT
    become the notification date.
    """

    # First look for a date/time in the nearest notification
    # container, before falling back to surrounding text.

    parents = []

    for tag in ["tr", "li", "td", "p", "div"]:
        parent = element.find_parent(tag)

        if parent:
            parents.append(parent)

    patterns = [
        r"\b\d{1,2}\s+"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"[a-z]*\s+\d{4}"
        r"(?:\s+\d{1,2}:\d{2}\s*(?:AM|PM))?",

        r"\b\d{1,2}\s+"
        r"(?:January|February|March|April|May|June|July|"
        r"August|September|October|November|December)"
        r"\s+\d{4}"
        r"(?:\s+\d{1,2}:\d{2}\s*(?:AM|PM))?",
    ]

    # Prefer the smallest parent that contains a posted date.
    for parent in parents:
        text = clean(
            parent.get_text(
                " ",
                strip=True
            )
        )

        for pattern in patterns:
            match = re.search(
                pattern,
                text,
                re.I
            )

            if match:
                return match.group(0)

    return ""

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
        match = re.search(
            pattern,
            text,
            re.I,
        )

        if match:
            return match.group(0)

    return ""


def parse_date(date_text):
    """Convert many date formats to datetime."""

    if not date_text:
        return None

    formats = [
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%d.%m.%Y",
        "%d-%m-%y",
        "%d/%m/%y",
        "%d %b %Y",
        "%d %B %Y",
        "%b %d %Y",
        "%B %d %Y",
        "%b %d, %Y",
        "%B %d, %Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(
                date_text.strip(),
                fmt,
            )
        except ValueError:
            pass

    return None


def get_context(element):
    """
    Collect useful text around an element.
    Prefer table rows / list items / notification boxes.
    """

    parts = []

    for parent_tag in [
        "tr",
        "li",
        "td",
        "p",
        "div",
        "article",
        "section",
    ]:
        parent = element.find_parent(parent_tag)

        if parent:
            text = clean(
                parent.get_text(
                    " ",
                    strip=True,
                )
            )

            if text:
                parts.append(text)

            # A table row or list item is usually enough.
            if parent_tag in ("tr", "li"):
                break

    return clean(" ".join(parts))


def parse_page(html, source):
    """
    Parse both:
    1. linked notifications
    2. notification boxes/text that may not have
       a useful <a> title
    """

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    for tag in soup(
        ["script", "style", "noscript"]
    ):
        tag.decompose()

    items = []
    seen = set()

    # -------------------------------------------------
    # METHOD 1: Parse every useful link
    # -------------------------------------------------

    for a in soup.find_all("a", href=True):

        title = clean(
            a.get_text(
                " ",
                strip=True,
            )
        )

        href = a.get(
            "href",
            "",
        ).strip()

        if not title:
            continue

        if href.startswith("#"):
            continue

        if href.lower().startswith(
            (
                "javascript:",
                "mailto:",
                "tel:",
            )
        ):
            continue

        url = urljoin(
            source,
            href,
        )

        context = get_context(a)

        if not context:
            context = title

        searchable = (
            f"{title} "
            f"{context} "
            f"{url}"
        )

        is_document = bool(
            re.search(
                r"\.(pdf|doc|docx|xls|xlsx)"
                r"(?:[?#].*)?$",
                url,
                re.I,
            )
        )

        if not (
            is_document
            or KEYWORDS.search(searchable)
        ):
            continue

        date = extract_posted_date(a)

        # Use surrounding text as title when
        # the link itself is only "Click Here".
        if title.lower() in {
            "click here",
            "here",
            "read more",
        }:
            better_title = context

            if len(better_title) > 180:
                better_title = better_title[:177] + "..."

            title_to_save = better_title
        else:
            title_to_save = title

        key = (
            title_to_save.lower(),
            url,
            date,
        )

        if key in seen:
            continue

        seen.add(key)

        description = context

        if (
            description.lower()
            == title_to_save.lower()
        ):
            description = ""

        if len(description) > 700:
            description = (
                description[:697]
                + "..."
            )

        items.append(
            {
                "title": title_to_save,
                "url": url,
                "date": date,
                "description": description,
                "source": source,
            }
        )

    # -------------------------------------------------
    # METHOD 2: Parse notification boxes themselves
    #
    # This is important for the COE page because a
    # notification can exist even when its visible
    # text is not the <a> text.
    # -------------------------------------------------

    candidates = []

    for element in soup.find_all(
        ["div", "td", "li", "p", "tr"]
    ):
        text = clean(
            element.get_text(
                " ",
                strip=True,
            )
        )

        if len(text) < 20:
            continue

        date = extract_date(text)

        if not date:
            continue

        if not KEYWORDS.search(text):
            continue

        # Avoid gigantic page containers.
        if len(text) > 1200:
            continue

        candidates.append(
            (
                element,
                text,
                date,
            )
        )

    for element, text, date in candidates:

        # Find a useful link inside this box.
        link = element.find(
            "a",
            href=True,
        )

        if link:
            url = urljoin(
                source,
                link.get(
                    "href",
                    "",
                ).strip(),
            )
        else:
            url = source

        # Prefer the full visible notification text.
        title = text

        # Remove repeated spaces.
        title = clean(title)

        if len(title) > 220:
            title = title[:217] + "..."

        key = (
            title.lower(),
            url,
            date,
        )

        if key in seen:
            continue

        seen.add(key)

        description = text

        if len(description) > 700:
            description = (
                description[:697]
                + "..."
            )

        items.append(
            {
                "title": title,
                "url": url,
                "date": date,
                "description": description,
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
            "Could not read old notifications:",
            error,
        )

    return []


def item_key(item):
    return (
        item.get(
            "title",
            "",
        ).strip().lower(),

        item.get(
            "url",
            "",
        ).strip(),

        item.get(
            "date",
            "",
        ).strip(),
    )


def sort_key(item):
    """
    Correctly sort:
    19 Sep 2026
    18 Sep 2026
    15-09-2026
    etc.
    """

    date_text = item.get(
        "date",
        "",
    )

    parsed = parse_date(
        date_text
    )

    if parsed:
        return parsed

    return datetime.min


# =====================================================
# MAIN
# =====================================================

old_items = load_old()

html = fetch(PRIMARY)

if html:
    items = parse_page(
        html,
        PRIMARY,
    )

    used_source = PRIMARY

else:
    print(
        "Primary COE website unavailable."
    )

    print(
        "Trying official fallback..."
    )

    html = fetch(FALLBACK)

    if html:
        items = parse_page(
            html,
            FALLBACK,
        )

        used_source = FALLBACK

    else:
        items = []
        used_source = ""


# If the primary page loaded but parser found
# nothing, try fallback too.

if not items and used_source == PRIMARY:

    print(
        "Primary page loaded but no "
        "notifications were detected."
    )

    fallback_html = fetch(
        FALLBACK
    )

    if fallback_html:

        fallback_items = parse_page(
            fallback_html,
            FALLBACK,
        )

        if fallback_items:
            items = fallback_items
            used_source = FALLBACK


# Never erase good existing data because
# the website temporarily failed.

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


# =====================================================
# Remove duplicates
# =====================================================

unique = {}

for item in items:

    key = item_key(item)

    if key not in unique:
        unique[key] = item

items = list(
    unique.values()
)


# =====================================================
# Sort newest first
# =====================================================

items.sort(
    key=sort_key,
    reverse=True,
)


# Keep feed reasonably sized.

items = items[:200]


# =====================================================
# Detect genuinely new notifications
# =====================================================

old_keys = {
    item_key(item)
    for item in old_items
}

new_items = [
    item
    for item in items
    if item_key(item)
    not in old_keys
]


# =====================================================
# Write JSON
# =====================================================

OUTPUT.parent.mkdir(
    parents=True,
    exist_ok=True,
)

OUTPUT.write_text(
    json.dumps(
        items,
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)


# =====================================================
# Report
# =====================================================

checked_at = datetime.now(
    timezone.utc
).isoformat()

print()
print("=" * 60)
print(
    "ANNA UNIVERSITY COE CHECK"
)
print("=" * 60)

print(
    f"Source: {used_source}"
)

print(
    f"Notifications found: {len(items)}"
)

print(
    f"New notifications: {len(new_items)}"
)

print(
    f"Checked at: {checked_at}"
)

print("=" * 60)

for item in new_items[:20]:

    print(
        "NEW:",
        item["date"],
        "|",
        item["title"],
        "->",
        item["url"],
    )

print("=" * 60)
