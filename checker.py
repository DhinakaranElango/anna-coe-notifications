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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

KEYWORDS = re.compile(
    r"(notification|circular|timetable|time table|exam|result|"
    r"hall.?ticket|revaluation|semester|schedule|fee|registration|"
    r"application|important|examination|arrear|regulation|"
    r"certificate|convocation|marksheet)",
    re.I,
)

GENERIC_LINKS = {
    "home",
    "login",
    "student login",
    "staff login",
    "contact",
    "contact us",
    "feedback",
    "about us",
    "gallery",
    "sitemap",
}


session = requests.Session()
session.headers.update(HEADERS)


def fetch(url):
    """Download a page with retries."""
    for attempt in range(3):
        try:
            print(f"Trying {url} (attempt {attempt + 1}/3)")
            response = session.get(url, timeout=30, verify=False)
            response.raise_for_status()

            if len(response.text) < 500:
                raise RuntimeError("Page response is unusually small")

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
    """Try to find a date without inventing one."""

    patterns = [
        r"\b\d{1,2}[-/\.]\d{1,2}[-/\.]\d{2,4}\b",
        r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}\b",
        r"\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b",
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(0)

    return ""


def parse_page(html, source):
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    items = []
    seen = set()

    for a in soup.find_all("a", href=True):

        title = clean(a.get_text(" ", strip=True))
        href = a.get("href", "").strip()

        if not title or len(title) < 5:
            continue

        if href.startswith("#"):
            continue

        if href.lower().startswith(
            ("javascript:", "mailto:", "tel:")
        ):
            continue

        url = urljoin(source, href)
        
        # Use the PDF filename when the website link says "Click Here"
if title.lower() in {"click here", "clickhere"}:
    filename = url.split("/")[-1].split("?")[0]
    filename = re.sub(r"\.(pdf|docx?|xlsx?)$", "", filename, flags=re.I)
    filename = re.sub(r"[_-]+", " ", filename)
    filename = re.sub(r"\s+", " ", filename).strip()

    if filename:
        title = filename.title()

        # Get useful surrounding text.
        context_parts = [title]

        for parent_tag in ["tr", "li", "p", "div"]:
            parent = a.find_parent(parent_tag)

            if parent:
                text = clean(parent.get_text(" ", strip=True))

                if text:
                    context_parts.append(text)

                # The table row is usually the most useful source.
                if parent_tag == "tr":
                    break

        context = clean(" ".join(context_parts))

        searchable = f"{title} {context} {url}"

        is_document = bool(
            re.search(
                r"\.(pdf|doc|docx|xls|xlsx)(?:[?#].*)?$",
                url,
                re.I,
            )
        )

        is_relevant = (
            is_document
            or bool(KEYWORDS.search(searchable))
        )

        if not is_relevant:
            continue

        if title.lower() in GENERIC_LINKS and not is_document:
            continue

        key = (title.lower(), url)

        if key in seen:
            continue

        seen.add(key)

        date = extract_date(context)

        description = context

        if description.lower() == title.lower():
            description = ""

        if len(description) > 500:
            description = description[:497] + "..."

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
            OUTPUT.read_text(encoding="utf-8")
        )

        if isinstance(data, list):
            return data

    except Exception as error:
        print(f"Could not read old notifications: {error}")

    return []


old_items = load_old()

# Try the original COE website first.
html = fetch(PRIMARY)

if html:
    items = parse_page(html, PRIMARY)
    used_source = PRIMARY
else:
    print("Primary COE website unavailable.")
    print("Trying official Controller of Examinations fallback...")

    html = fetch(FALLBACK)

    if html:
        items = parse_page(html, FALLBACK)
        used_source = FALLBACK
    else:
        items = []
        used_source = ""

# If a page loaded but produced nothing useful,
# try the fallback official COE website too.
if not items and used_source == PRIMARY:

    print("Primary page loaded but no notifications were detected.")
    print("Trying official fallback source...")

    fallback_html = fetch(FALLBACK)

    if fallback_html:
        fallback_items = parse_page(
            fallback_html,
            FALLBACK
        )

        if fallback_items:
            items = fallback_items
            used_source = FALLBACK


if not items:
    print("WARNING: No notifications detected.")

    # IMPORTANT:
    # Do not erase existing working data just because
    # the COE website temporarily changed or failed.
    if old_items:
        print(
            f"Keeping existing {len(old_items)} notifications."
        )
    else:
        print("No previous notifications exist yet.")

    raise SystemExit(0)


# Remove duplicates one more time.
unique = {}

for item in items:
    key = (
        item.get("title", "").strip().lower(),
        item.get("url", "").strip(),
    )

    unique[key] = item

items = list(unique.values())


# Put dated notices first.
def sort_key(item):
    date_text = item.get("date", "")

    match = re.search(
        r"(\d{1,2})[-/\.](\d{1,2})[-/\.](\d{2,4})",
        date_text,
    )

    if match:
        day, month, year = match.groups()

        if len(year) == 2:
            year = "20" + year

        try:
            return datetime(
                int(year),
                int(month),
                int(day),
            )
        except ValueError:
            pass

    return datetime.min


items.sort(
    key=sort_key,
    reverse=True,
)


# Keep the feed reasonably sized.
items = items[:200]


old_keys = {
    (
        item.get("title", "").strip().lower(),
        item.get("url", "").strip(),
    )
    for item in old_items
}

new_items = [
    item
    for item in items
    if (
        item.get("title", "").strip().lower(),
        item.get("url", "").strip(),
    )
    not in old_keys
]


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


checked_at = datetime.now(
    timezone.utc
).isoformat()

print()
print("=" * 60)
print("ANNA UNIVERSITY COE CHECK")
print("=" * 60)
print(f"Source: {used_source}")
print(f"Notifications found: {len(items)}")
print(f"New notifications: {len(new_items)}")
print(f"Checked at: {checked_at}")
print("=" * 60)

for item in new_items[:20]:
    print(
        "NEW:",
        item["title"],
        "->",
        item["url"],
    )
