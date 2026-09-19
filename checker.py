import json
import re
import time
import urllib3

from pathlib import Path
from urllib.parse import urljoin
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup


# ============================================================
# CONFIGURATION
# ============================================================

ROOT = Path(__file__).resolve().parent

OUTPUT = ROOT / "data" / "notifications.json"

PRIMARY = "https://coe.annauniv.edu/home/index.php"

FALLBACK = "https://aucoe.annauniv.edu/"


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
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


# Suppress the SSL warning produced because the
# COE website may use an older certificate.

urllib3.disable_warnings(
    urllib3.exceptions.InsecureRequestWarning
)


KEYWORDS = re.compile(
    r"(notification|circular|timetable|time table|exam|"
    r"result|hall.?ticket|revaluation|semester|schedule|"
    r"fee|registration|application|important|examination|"
    r"arrear|regulation|certificate|convocation|marksheet|"
    r"academic|assessment|candidate|photograph|"
    r"answer scripts|answer script|institutions)",
    re.I,
)


session = requests.Session()

session.headers.update(HEADERS)


# ============================================================
# BASIC HELPERS
# ============================================================

def clean(text):
    """Remove extra whitespace."""

    return " ".join(
        text.split()
    )


# ============================================================
# DATE EXTRACTION
# ============================================================

def extract_posted_datetime(text):
    """
    Extract the COE POSTED date/time.

    Example:

        19 Sep 2026 03:13 PM

    returns:

        19 Sep 2026 03:13 PM

    This function deliberately prefers a date followed
    by a time because the notification text may contain
    another date such as an extension deadline:

        21-09-2026

    We do NOT want that deadline to become the notification
    date.
    """

    if not text:
        return ""

    patterns = [

        # Example:
        # 19 Sep 2026 03:13 PM

        r"\b"
        r"\d{1,2}\s+"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"[a-z]*\s+\d{4}"
        r"\s+\d{1,2}:\d{2}\s*(?:AM|PM)"
        r"\b",

        # Example:
        # 19 September 2026 03:13 PM

        r"\b"
        r"\d{1,2}\s+"
        r"(?:January|February|March|April|May|June|July|"
        r"August|September|October|November|December)"
        r"\s+\d{4}"
        r"\s+\d{1,2}:\d{2}\s*(?:AM|PM)"
        r"\b",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.I
        )

        if match:

            return clean(
                match.group(0)
            )

    return ""


def extract_date_only(text):
    """
    Fallback date extraction.

    Used only when a date/time is not available.
    """

    if not text:
        return ""

    patterns = [

        # 19-09-2026
        r"\b\d{1,2}[-/\.]\d{1,2}[-/\.]\d{2,4}\b",

        # 19 Sep 2026
        r"\b\d{1,2}\s+"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"[a-z]*\s+\d{4}\b",

        # 19 September 2026
        r"\b\d{1,2}\s+"
        r"(?:January|February|March|April|May|June|July|"
        r"August|September|October|November|December)"
        r"\s+\d{4}\b",

        # Sep 19 2026
        r"\b"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"[a-z]*\s+\d{1,2},?\s+\d{4}\b",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.I
        )

        if match:

            return match.group(0)

    return ""


def extract_date(text):
    """
    Compatibility function.

    IMPORTANT:
    If the COE page contains:

        19 Sep 2026 03:13 PM

    and later:

        21-09-2026

    the first value is preferred because it is the
    actual notification posting date/time.
    """

    posted = extract_posted_datetime(
        text
    )

    if posted:

        # Return only the date portion.
        match = re.search(
            r"\d{1,2}\s+"
            r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
            r"[a-z]*\s+\d{4}",
            posted,
            re.I
        )

        if match:
            return match.group(0)

    return extract_date_only(
        text
    )


# ============================================================
# FETCH COE WEBSITE
# ============================================================

def fetch(url):

    for attempt in range(3):

        try:

            print(
                f"Trying {url} "
                f"(attempt {attempt + 1}/3)"
            )

            # Cache-busting query parameter
            params = {
                "_": int(time.time())
            }

            response = session.get(
                url,
                timeout=45,
                verify=False,
                params=params
            )

            response.raise_for_status()

            if len(response.text) < 500:

                raise RuntimeError(
                    "Page response is unusually small"
                )

            print(
                f"Successfully fetched {url}"
            )

            return response.text

        except Exception as error:

            print(
                f"Fetch failed: {error}"
            )

            if attempt < 2:

                time.sleep(
                    2 ** attempt
                )

    return None


# ============================================================
# FIND NOTIFICATION BLOCKS
# ============================================================

def find_notification_blocks(soup):

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

        for element in soup.select(
            selector
        ):

            text = clean(
                element.get_text(
                    " ",
                    strip=True
                )
            )

            if not text:
                continue

            has_date = bool(
                re.search(
                    r"\b\d{1,2}\s+"
                    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
                    r"[a-z]*\s+\d{4}\b",
                    text,
                    re.I
                )
            )

            has_keyword = bool(
                KEYWORDS.search(text)
            )

            if not has_date and not has_keyword:
                continue

            # Ignore huge page-level containers.
            if len(text) > 1500:
                continue

            key = text[:1000]

            if key in seen:
                continue

            seen.add(key)

            blocks.append(
                element
            )

    return blocks


# ============================================================
# PARSE COE PAGE
# ============================================================

def parse_page(
    html,
    source
):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    # Remove scripts/styles.
    for tag in soup(
        ["script", "style", "noscript"]
    ):

        tag.decompose()

    items = []

    seen_urls = set()

    # --------------------------------------------------------
    # METHOD 1
    # Notification blocks
    # --------------------------------------------------------

    blocks = find_notification_blocks(
        soup
    )

    for block in blocks:

        block_text = clean(
            block.get_text(
                " ",
                strip=True
            )
        )

        # ----------------------------------------------------
        # IMPORTANT DATE LOGIC
        # ----------------------------------------------------
        #
        # Prefer the date + time.
        #
        # Example:
        #
        # 19 Sep 2026 03:13 PM
        #
        # instead of:
        #
        # 21-09-2026
        #
        # mentioned later in the message.
        # ----------------------------------------------------

        posted_datetime = (
            extract_posted_datetime(
                block_text
            )
        )

        posted_date = (
            extract_date(
                block_text
            )
        )

        links = block.find_all(
            "a",
            href=True
        )

        for link in links:

            href = link.get(
                "href",
                ""
            ).strip()

            link_text = clean(
                link.get_text(
                    " ",
                    strip=True
                )
            )

            if not href:
                continue

            if href.startswith("#"):
                continue

            if href.lower().startswith(
                (
                    "javascript:",
                    "mailto:",
                    "tel:"
                )
            ):
                continue

            url = urljoin(
                source,
                href
            )

            lower_url = url.lower()

            is_document = bool(
                re.search(
                    r"\.(pdf|doc|docx|xls|xlsx)"
                    r"(?:[?#].*)?$",
                    lower_url,
                    re.I
                )
            )

            is_click_link = (
                link_text.lower()
                in {
                    "click here",
                    "clickhere",
                    "here",
                    "read more"
                }
            )

            if (
                not is_document
                and not is_click_link
            ):
                continue

            if url in seen_urls:
                continue

            seen_urls.add(url)

            # ------------------------------------------------
            # TITLE
            # ------------------------------------------------

            title = block_text

            # Remove the POSTED date/time from beginning.
            title = re.sub(
                r"^\s*\d{1,2}\s+"
                r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
                r"[a-z]*\s+\d{4}"
                r"(?:\s+\d{1,2}:\d{2}\s*(?:AM|PM))?"
                r"\s*",
                "",
                title,
                flags=re.I
            )

            # Remove Click Here at the end.
            title = re.sub(
                r"\s*click\s*here\s*$",
                "",
                title,
                flags=re.I
            )

            title = clean(
                title
            )

            if not title:

                title = (
                    "Anna University "
                    "COE Notification"
                )

            if len(title) > 300:

                title = (
                    title[:297]
                    + "..."
                )

            # ------------------------------------------------
            # DESCRIPTION
            # ------------------------------------------------

            description = block_text

            if len(description) > 800:

                description = (
                    description[:797]
                    + "..."
                )

            # ------------------------------------------------
            # DATE TO SAVE
            # ------------------------------------------------

            if posted_datetime:

                saved_date = (
                    posted_datetime
                )

            elif posted_date:

                saved_date = (
                    posted_date
                )

            else:

                saved_date = ""

            items.append(
                {
                    "title": title,

                    "url": url,

                    "date": saved_date,

                    "description":
                        description,

                    "source": source
                }
            )

    # --------------------------------------------------------
    # METHOD 2
    # Generic PDF/document links
    # --------------------------------------------------------

    for link in soup.find_all(
        "a",
        href=True
    ):

        href = link.get(
            "href",
            ""
        ).strip()

        if not href:
            continue

        if href.startswith("#"):
            continue

        if href.lower().startswith(
            (
                "javascript:",
                "mailto:",
                "tel:"
            )
        ):
            continue

        url = urljoin(
            source,
            href
        )

        if url in seen_urls:
            continue

        is_document = bool(
            re.search(
                r"\.(pdf|doc|docx|xls|xlsx)"
                r"(?:[?#].*)?$",
                url,
                re.I
            )
        )

        if not is_document:
            continue

        link_text = clean(
            link.get_text(
                " ",
                strip=True
            )
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
            f"{link_text} "
            f"{context} "
            f"{url}"
        )

        if not KEYWORDS.search(
            searchable
        ):
            continue

        seen_urls.add(
            url
        )

        posted_datetime = (
            extract_posted_datetime(
                context
            )
        )

        posted_date = (
            extract_date(
                context
            )
        )

        title = (
            context
            or link_text
            or "Anna University COE Notification"
        )

        if len(title) > 300:

            title = (
                title[:297]
                + "..."
            )

        if posted_datetime:

            saved_date = (
                posted_datetime
            )

        else:

            saved_date = (
                posted_date
            )

        items.append(
            {
                "title": title,

                "url": url,

                "date": saved_date,

                "description":
                    context[:800],

                "source": source
            }
        )

    return items


# ============================================================
# LOAD OLD DATA
# ============================================================

def load_old():

    if not OUTPUT.exists():

        return []

    try:

        data = json.loads(
            OUTPUT.read_text(
                encoding="utf-8"
            )
        )

        if isinstance(
            data,
            list
        ):

            return data

    except Exception as error:

        print(
            "Could not read old "
            f"notifications: {error}"
        )

    return []


# ============================================================
# DATE SORTING
# ============================================================

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

    # Search inside larger text.

    match = re.search(
        r"(\d{1,2}\s+"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"[a-z]*\s+\d{4}"
        r"(?:\s+\d{1,2}:\d{2}\s*(?:AM|PM))?)",
        date_text,
        re.I
    )

    if match:

        value = clean(
            match.group(1)
        )

        for fmt in formats:

            try:

                return datetime.strptime(
                    value,
                    fmt
                )

            except ValueError:

                pass

    return datetime.min


# ============================================================
# MAIN
# ============================================================

old_items = load_old()


print(
    "=" * 60
)

print(
    "ANNA UNIVERSITY COE CHECK"
)

print(
    "=" * 60
)


html = fetch(
    PRIMARY
)


used_source = PRIMARY

items = []


# ------------------------------------------------------------
# Primary website
# ------------------------------------------------------------

if html:

    items = parse_page(
        html,
        PRIMARY
    )


# ------------------------------------------------------------
# Fallback website
# ------------------------------------------------------------

if not items:

    print(
        "Primary COE page produced "
        "no notifications."
    )

    print(
        "Trying official fallback..."
    )

    fallback_html = fetch(
        FALLBACK
    )

    if fallback_html:

        fallback_items = parse_page(
            fallback_html,
            FALLBACK
        )

        if fallback_items:

            items = fallback_items

            used_source = FALLBACK


# ============================================================
# SAFETY
# ============================================================

# NEVER delete good existing data just because
# the COE website temporarily failed.

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


# ============================================================
# REMOVE DUPLICATES
# ============================================================

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
            )
            .strip()
            .lower()
        )

    unique[key] = item


items = list(
    unique.values()
)


# ============================================================
# SORT NEWEST FIRST
# ============================================================

items.sort(
    key=date_sort_key,
    reverse=True
)


# ============================================================
# KEEP MAXIMUM 200
# ============================================================

items = items[:200]


# ============================================================
# DETECT NEW NOTIFICATIONS
# ============================================================

old_keys = {

    (
        item.get(
            "title",
            ""
        ).strip().lower(),

        item.get(
            "url",
            ""
        ).strip()
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
