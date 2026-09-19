import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin
from datetime import datetime

import requests
import urllib3
from bs4 import BeautifulSoup


# ============================================================
# ANNA UNIVERSITY COE NOTIFICATION CHECKER
# ============================================================

PRIMARY_URL = "https://coe.annauniv.edu/home/index.php"
FALLBACK_URL = "https://aucoe.annauniv.edu/"

OUTPUT_FILE = Path("data/notifications.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ============================================================
# DATE PATTERNS
# ============================================================

# Example:
# 19 Sep 2026 03:13 PM
# 19 September 2026 03:13 PM
DATE_TIME_RE = re.compile(
    r"\b"
    r"(\d{1,2})"
    r"\s+"
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|"
    r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+"
    r"(\d{4})"
    r"(?:\s+|,?\s*)"
    r"(\d{1,2}):(\d{2})"
    r"\s*"
    r"(AM|PM)"
    r"\b",
    re.IGNORECASE,
)

# Numeric dates:
# 19-09-2026
# 19/09/2026
# 19.09.2026
NUMERIC_DATE_RE = re.compile(
    r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b"
)

# Text dates:
# 19 Sep 2026
# 19 September 2026
TEXT_DATE_RE = re.compile(
    r"\b"
    r"(\d{1,2})"
    r"\s+"
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|"
    r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+"
    r"(\d{4})"
    r"\b",
    re.IGNORECASE,
)


MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


# ============================================================
# HELPERS
# ============================================================

def clean_text(value):
    """Normalize whitespace."""
    if not value:
        return ""

    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def normalize_url(url):
    """Convert relative URL to absolute URL."""
    if not url:
        return ""

    url = url.strip()

    if url.startswith("#"):
        return ""

    return urljoin(PRIMARY_URL, url)


def is_document_url(url):
    """Return True for likely notification/document links."""
    if not url:
        return False

    lower = url.lower()

    document_extensions = (
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".jpg",
        ".jpeg",
        ".png",
    )

    keywords = (
        "pdf",
        "notification",
        "circular",
        "coe",
        "result",
        "revaluation",
        "valuation",
        "timetable",
        "time-table",
        "exam",
        "examination",
        "application",
        "download",
    )

    if lower.endswith(document_extensions):
        return True

    return any(keyword in lower for keyword in keywords)


def is_notification_text(text):
    """Check whether text looks like a COE notification."""

    if not text:
        return False

    lower = text.lower()

    keywords = (
        "notification",
        "web portal",
        "revaluation",
        "examination",
        "examinations",
        "examination",
        "answer script",
        "answer scripts",
        "hall ticket",
        "results",
        "result",
        "timetable",
        "time table",
        "valuation",
        "application",
        "circular",
        "important",
        "kind attention",
        "institutions",
        "students",
        "ug/pg",
        "ug / pg",
        "ph.d",
        "ph.d.",
    )

    return any(keyword in lower for keyword in keywords)


def parse_text_date(match):
    """Convert a regex date match into DD-MM-YYYY."""

    if not match:
        return None

    try:
        day = int(match.group(1))
        month_name = match.group(2).lower()
        year = int(match.group(3))

        month = MONTHS.get(month_name)

        if not month:
            return None

        dt = datetime(year, month, day)

        return dt.strftime("%d-%m-%Y")

    except Exception:
        return None


def parse_numeric_date(match):
    """Convert numeric date into DD-MM-YYYY."""

    if not match:
        return None

    try:
        day = int(match.group(1))
        month = int(match.group(2))
        year = int(match.group(3))

        # Basic validation
        dt = datetime(year, month, day)

        return dt.strftime("%d-%m-%Y")

    except Exception:
        return None


def extract_date_time(text):
    """
    Extract the strongest posting date from text.

    Priority:
    1. Date + time
    2. Text date
    3. Numeric date

    This is important because a notification may contain:
        19 Sep 2026 03:13 PM
    and also:
        21-09-2026

    The posting timestamp must win.
    """

    if not text:
        return None

    # --------------------------------------------------------
    # 1. DATE + TIME
    # --------------------------------------------------------

    matches = list(DATE_TIME_RE.finditer(text))

    if matches:
        # Use the first timestamp.
        match = matches[0]

        try:
            day = int(match.group(1))
            month_name = match.group(2).lower()
            year = int(match.group(3))

            hour = int(match.group(4))
            minute = int(match.group(5))
            am_pm = match.group(6).upper()

            month = MONTHS.get(month_name)

            if month:
                if am_pm == "PM" and hour != 12:
                    hour += 12

                if am_pm == "AM" and hour == 12:
                    hour = 0

                dt = datetime(
                    year,
                    month,
                    day,
                    hour,
                    minute,
                )

                return dt.strftime("%d-%m-%Y")

        except Exception:
            pass

    # --------------------------------------------------------
    # 2. TEXT DATE
    # --------------------------------------------------------

    match = TEXT_DATE_RE.search(text)

    if match:
        date = parse_text_date(match)

        if date:
            return date

    # --------------------------------------------------------
    # 3. NUMERIC DATE
    # --------------------------------------------------------

    match = NUMERIC_DATE_RE.search(text)

    if match:
        date = parse_numeric_date(match)

        if date:
            return date

    return None


def extract_timestamp(text):
    """Return the exact posting timestamp if available."""

    if not text:
        return None

    match = DATE_TIME_RE.search(text)

    if not match:
        return None

    try:
        day = int(match.group(1))
        month_name = match.group(2).lower()
        year = int(match.group(3))

        hour = int(match.group(4))
        minute = int(match.group(5))
        am_pm = match.group(6).upper()

        month = MONTHS.get(month_name)

        if not month:
            return None

        if am_pm == "PM" and hour != 12:
            hour += 12

        if am_pm == "AM" and hour == 12:
            hour = 0

        dt = datetime(
            year,
            month,
            day,
            hour,
            minute,
        )

        return dt

    except Exception:
        return None


def looks_like_date_only(text):
    """Detect if a string is mainly a date."""

    if not text:
        return False

    text = clean_text(text)

    if NUMERIC_DATE_RE.fullmatch(text):
        return True

    if TEXT_DATE_RE.fullmatch(text):
        return True

    if DATE_TIME_RE.fullmatch(text):
        return True

    return False


def title_from_link(anchor):
    """
    Extract a useful title from an <a>.
    """

    text = clean_text(anchor.get_text(" ", strip=True))

    if text:
        return text

    href = anchor.get("href", "")

    return clean_text(href)


def surrounding_text(anchor):
    """
    Get useful text around a link.

    We intentionally search multiple parent levels because
    Anna University may place the timestamp and link in
    different HTML elements.
    """

    pieces = []

    # Anchor itself
    anchor_text = clean_text(anchor.get_text(" ", strip=True))

    if anchor_text:
        pieces.append(anchor_text)

    # Parent levels
    current = anchor

    for _ in range(6):
        current = current.parent

        if current is None:
            break

        text = clean_text(current.get_text(" ", strip=True))

        if text:
            pieces.append(text)

    # Remove duplicates while preserving order
    result = []

    seen = set()

    for piece in pieces:
        if piece not in seen:
            result.append(piece)
            seen.add(piece)

    return result


def find_nearby_timestamp(anchor, soup):
    """
    Search around an anchor for a posting timestamp.

    We don't require the timestamp to be in the same container.
    """

    # --------------------------------------------------------
    # Search parent elements
    # --------------------------------------------------------

    current = anchor

    for _ in range(8):
        current = current.parent

        if current is None:
            break

        text = clean_text(current.get_text(" ", strip=True))

        if DATE_TIME_RE.search(text):
            timestamp = extract_timestamp(text)

            if timestamp:
                return timestamp

    # --------------------------------------------------------
    # Search previous/next text nodes
    # --------------------------------------------------------

    for element in anchor.find_all_previous(string=True, limit=30):

        text = clean_text(str(element))

        if not text:
            continue

        timestamp = extract_timestamp(text)

        if timestamp:
            return timestamp

    for element in anchor.find_all_next(string=True, limit=30):

        text = clean_text(str(element))

        if not text:
            continue

        timestamp = extract_timestamp(text)

        if timestamp:
            return timestamp

    # --------------------------------------------------------
    # Search whole page as final fallback
    # --------------------------------------------------------

    page_text = clean_text(soup.get_text(" ", strip=True))

    timestamp = extract_timestamp(page_text)

    return timestamp


def get_best_context(anchor):
    """
    Build notification text from nearby HTML.
    """

    contexts = surrounding_text(anchor)

    if not contexts:
        return title_from_link(anchor)

    # Prefer the smallest useful context that contains
    # notification-related wording.
    for context in contexts:
        if is_notification_text(context):
            return context

    # Otherwise use the largest available nearby context.
    return max(contexts, key=len)


def clean_notification_title(text):
    """
    Clean title without accidentally removing useful dates.
    """

    text = clean_text(text)

    # Remove repeated spaces
    text = re.sub(r"\s+", " ", text)

    # Remove common UI-only prefixes
    text = re.sub(
        r"^(click here|click|view|download)\s*[:\-]?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    return text.strip()


def make_record(anchor, context, timestamp):
    """
    Create a notification record.
    """

    href = normalize_url(anchor.get("href", ""))

    if not href:
        return None

    title = clean_notification_title(title_from_link(anchor))

    if not title:
        title = clean_notification_title(context)

    if not title:
        return None

    # Avoid using a date-only link as the title.
    if looks_like_date_only(title):
        title = clean_notification_title(context)

    if not title:
        return None

    # Notification date comes from timestamp first.
    date = None

    if timestamp:
        date = timestamp.strftime("%d-%m-%Y")

    if not date:
        date = extract_date_time(context)

    if not date:
        date = extract_date_time(title)

    if not date:
        return None

    description = clean_text(context)

    # Prevent giant page-sized descriptions.
    if len(description) > 1500:
        description = description[:1500].rstrip() + "..."

    return {
        "title": title,
        "url": href,
        "date": date,
        "description": description,
        "source": PRIMARY_URL,
    }


# ============================================================
# HTTP
# ============================================================

def fetch_page(url, attempts=3):
    """Download the COE page."""

    for attempt in range(1, attempts + 1):

        try:
            print(
                f"Fetching {url} "
                f"(attempt {attempt}/{attempts})"
            )

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=30,
                verify=False,
            )

            response.raise_for_status()

            content = response.content

            print(
                f"Fetched successfully: "
                f"{len(content)} bytes"
            )

            return response.text

        except Exception as exc:

            print(
                f"Fetch failed: {type(exc).__name__}: {exc}"
            )

            if attempt < attempts:
                time.sleep(3)

    return None


# ============================================================
# HTML PARSER
# ============================================================

def parse_page(html):
    """
    Parse COE notifications.

    Important:
    We DO NOT assume:
        timestamp + link = same HTML element.

    Instead we:
        1. inspect all links
        2. identify notification-like links
        3. search around each link for timestamp
        4. use the page structure as fallback
    """

    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")

    records = []

    all_links = soup.find_all("a", href=True)

    print(f"Total links found: {len(all_links)}")

    # ========================================================
    # STEP 1 — Find all timestamp strings on page
    # ========================================================

    page_text = clean_text(
        soup.get_text(" ", strip=True)
    )

    timestamps = []

    for match in DATE_TIME_RE.finditer(page_text):

        raw = clean_text(match.group(0))
        dt = extract_timestamp(raw)

        if dt:
            timestamps.append(
                (raw, dt)
            )

    print(
        f"Posting timestamps found on page: "
        f"{len(timestamps)}"
    )

    for raw, dt in timestamps[:20]:
        print(
            f"  TIMESTAMP: {raw} "
            f"-> {dt.strftime('%d-%m-%Y %I:%M %p')}"
        )

    # ========================================================
    # STEP 2 — Inspect links
    # ========================================================

    candidates = []

    for index, anchor in enumerate(all_links):

        href = normalize_url(
            anchor.get("href", "")
        )

        if not href:
            continue

        link_text = clean_text(
            anchor.get_text(" ", strip=True)
        )

        contexts = surrounding_text(anchor)

        combined = " ".join(contexts)

        # ----------------------------------------------------
        # Determine whether this looks like notification
        # ----------------------------------------------------

        notification_like = (
            is_document_url(href)
            or is_notification_text(link_text)
            or is_notification_text(combined)
        )

        if not notification_like:
            continue

        candidates.append(
            (
                index,
                anchor,
                href,
                link_text,
                combined,
            )
        )

    print(
        f"Notification-like links found: "
        f"{len(candidates)}"
    )

    # ========================================================
    # STEP 3 — Build records
    # ========================================================

    for index, anchor, href, link_text, combined in candidates:

        timestamp = find_nearby_timestamp(
            anchor,
            soup,
        )

        context = get_best_context(anchor)

        record = make_record(
            anchor,
            context,
            timestamp,
        )

        if record:
            records.append(record)

            timestamp_text = (
                timestamp.strftime(
                    "%d-%m-%Y %I:%M %p"
                )
                if timestamp
                else "NO TIMESTAMP"
            )

            print()
            print("FOUND NOTIFICATION")
            print(f"  Link: {record['url']}")
            print(f"  Title: {record['title'][:180]}")
            print(f"  Date: {record['date']}")
            print(f"  Timestamp: {timestamp_text}")

    # ========================================================
    # STEP 4 — De-duplicate
    # ========================================================

    unique = {}

    for record in records:

        key = (
            record["url"].strip().lower()
            or (
                record["title"].strip().lower(),
                record["date"],
            )
        )

        if key not in unique:
            unique[key] = record
            continue

        # Prefer the record with the longer description.
        old = unique[key]

        if len(record["description"]) > len(
            old["description"]
        ):
            unique[key] = record

    records = list(unique.values())

    # ========================================================
    # STEP 5 — Sort newest first
    # ========================================================

    def sort_key(record):

        try:
            return datetime.strptime(
                record["date"],
                "%d-%m-%Y",
            )
        except Exception:
            return datetime.min

    records.sort(
        key=sort_key,
        reverse=True,
    )

    return records


# ============================================================
# JSON
# ============================================================

def load_existing():
    """Load existing notifications.json."""

    if not OUTPUT_FILE.exists():
        return []

    try:

        with OUTPUT_FILE.open(
            "r",
            encoding="utf-8",
        ) as file:

            data = json.load(file)

        if isinstance(data, list):
            return data

        return []

    except Exception as exc:

        print(
            f"Could not read existing JSON: "
            f"{type(exc).__name__}: {exc}"
        )

        return []


def save_records(records):
    """Save notification records."""

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with OUTPUT_FILE.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            records,
            file,
            indent=2,
            ensure_ascii=False,
        )

        file.write("\n")


def record_key(record
