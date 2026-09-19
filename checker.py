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


# Anna University COE may use an older SSL certificate.
urllib3.disable_warnings(
    urllib3.exceptions.InsecureRequestWarning
)


session = requests.Session()
session.headers.update(HEADERS)


# ============================================================
# DATE PATTERNS
# ============================================================

# Example:
# 19 Sep 2026 03:13 PM

DATE_TIME_RE = re.compile(
    r"\b"
    r"\d{1,2}\s+"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"[a-z]*\s+\d{4}"
    r"\s+\d{1,2}:\d{2}\s*(?:AM|PM)"
    r"\b",
    re.I,
)


# Examples:
# 19-09-2026
# 19/09/2026
# 19 Sep 2026
# 19 September 2026

DATE_RE = re.compile(
    r"\b(?:"
    r"\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}"
    r"|"
    r"\d{1,2}\s+"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"[a-z]*\s+\d{4}"
    r"|"
    r"\d{1,2}\s+"
    r"(?:January|February|March|April|May|June|July|"
    r"August|September|October|November|December)"
    r"\s+\d{4}"
    r")\b",
    re.I,
)


# Words commonly appearing in COE notifications.
KEYWORDS = re.compile(
    r"(notification|circular|timetable|time table|exam|"
    r"examination|result|hall.?ticket|revaluation|semester|"
    r"schedule|registration|application|important|arrear|"
    r"regulation|certificate|convocation|marksheet|academic|"
    r"assessment|candidate|photograph|answer script|"
    r"institutions)",
    re.I,
)


# ============================================================
# HELPER
# ============================================================

def clean(text):
    """Remove unnecessary spaces and line breaks."""

    return " ".join(
        str(text or "").split()
    )


# ============================================================
# DATE PARSING
# ============================================================

def parse_datetime(value):
    """
    Convert notification dates into datetime objects
    for correct newest-first sorting.
    """

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

            return datetime.strptime(
                value,
                fmt
            )

        except ValueError:

            pass

    return datetime.min


# ============================================================
# GET NOTIFICATION POSTED DATE
# ============================================================

def posted_date_from_text(text):
    """
    IMPORTANT:

    The COE notification can contain two dates.

    Example:

        19 Sep 2026 03:13 PM
        ...
        application extended up to 21-09-2026

    We want:

        19 Sep 2026 03:13 PM

    NOT:

        21-09-2026
    """

    text = clean(text)

    # First priority:
    # date + time
    match = DATE_TIME_RE.search(
        text
    )

    if match:

        return clean(
            match.group(0)
        )

    # Fallback:
    # ordinary date
    match = DATE_RE.search(
        text
    )

    if match:

        return clean(
            match.group(0)
        )

    return ""


# ============================================================
# FETCH WEBSITE
# ============================================================

def fetch(url):

    for attempt in range(3):

        try:

            print(
                f"Fetching {url} "
                f"(attempt {attempt + 1}/3)"
            )

            response = session.get(
                url,

                timeout=45,

                verify=False,

                params={
                    "_": int(time.time())
                }
            )

            response.raise_for_status()

            if len(response.text) < 500:

                raise RuntimeError(
                    "COE response is too small"
                )

            print(
                "COE website fetched successfully."
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
# FIND THE ACTUAL NOTIFICATION CONTAINER
# ============================================================

def smallest_posted_container(anchor):
    """
    Starting from a notification link, find the smallest
    surrounding HTML element that contains the posted
    date/time.

    This is important because a notification may contain
    another date such as an extension deadline.
    """

    candidates = []

    for tag in (
        "tr",
        "li",
        "td",
        "p",
        "div",
        "article",
        "section"
    ):

        parent = anchor.find_parent(
            tag
        )

        if parent:

            text = clean(
                parent.get_text(
                    " ",
                    strip=True
                )
            )

            if DATE_TIME_RE.search(
                text
            ):

                candidates.append(
                    (
                        len(text),
                        parent
                    )
                )

    if candidates:

        candidates.sort(
            key=lambda item: item[0]
        )

        return candidates[0][1]

    return None


# ============================================================
# CREATE TITLE
# ============================================================

def notification_title(text):

    text = clean(text)

    # Remove posted date/time from beginning.
    text = re.sub(
        r"^\s*"
        + DATE_TIME_RE.pattern
        + r"\s*",
        "",
        text,
        flags=re.I
    )

    # Remove "Click Here".
    text = re.sub(
        r"\s*click\s*here\s*$",
        "",
        text,
        flags=re.I
    )

    text = clean(
        text
    )

    if not text:

        text = (
            "Anna University "
            "COE Notification"
        )

    return text


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

    # Remove unnecessary elements.
    for tag in soup(
        [
            "script",
            "style",
            "noscript"
        ]
    ):

        tag.decompose()

    items = []

    seen = set()


    # ========================================================
    # METHOD 1
    # Find notification links
    # ========================================================

    for anchor in soup.find_all(
        "a",
        href=True
    ):

        href = clean(
            anchor.get(
                "href",
                ""
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


        link_text = clean(
            anchor.get_text(
                " ",
                strip=True
            )
        )


        # Find the notification box.
        container = (
            smallest_posted_container(
                anchor
            )
        )


        if container:

            context = clean(
                container.get_text(
                    " ",
                    strip=True
                )
            )

        else:

            parent = (
                anchor.find_parent("tr")
                or anchor.find_parent("li")
                or anchor.find_parent("p")
                or anchor.find_parent("div")
            )

            if parent:

                context = clean(
                    parent.get_text(
                        " ",
                        strip=True
                    )
                )

            else:

                context = link_text


        # Check whether this is likely a COE notification.
        is_document = bool(
            re.search(
                r"\.(pdf|doc|docx|xls|xlsx)"
                r"(?:[?#].*)?$",
                url,
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


        if not (
            is_document
            or is_click_link
            or KEYWORDS.search(context)
        ):

            continue


        # ----------------------------------------------------
        # DATE
        # ----------------------------------------------------

        date = posted_date_from_text(
            context
        )


        if not date:

            continue


        # ----------------------------------------------------
        # REMOVE DUPLICATE URL
        # ----------------------------------------------------

        if url in seen:

            continue

        seen.add(
            url
        )


        # ----------------------------------------------------
        # TITLE
        # ----------------------------------------------------

        title = notification_title(
            context
        )


        if len(title) > 300:

            title = (
                title[:297]
                + "..."
            )


        # ----------------------------------------------------
        # DESCRIPTION
        # ----------------------------------------------------

        description = context

        if len(description) > 800:

            description = (
                description[:797]
                + "..."
            )


        # ----------------------------------------------------
        # SAVE
        # ----------------------------------------------------

        items.append(
            {
                "title": title,

                "url": url,

                "date": date,

                "description": description,

                "source": source
            }
        )


    # ========================================================
    # METHOD 2
    # Fallback PDF/document search
    # ========================================================

    if not items:

        for anchor in soup.find_all(
            "a",
            href=True
        ):

            href = clean(
                anchor.get(
                    "href",
                    ""
                )
            )

            if not href:

                continue


            url = urljoin(
                source,
                href
            )


            if not re.search(
                r"\.(pdf|doc|docx|xls|xlsx)"
                r"(?:[?#].*)?$",
                url,
                re.I
            ):

                continue


            parent = (
                anchor.find_parent("tr")
                or anchor.find_parent("li")
                or anchor.find_parent("p")
                or anchor.find_parent("div")
            )


            if parent:

                context = clean(
                    parent.get_text(
                        " ",
                        strip=True
                    )
                )

            else:

                context = clean(
                    anchor.get_text(
                        " ",
                        strip=True
                    )
                )


            if not KEYWORDS.search(
                context
            ):

                continue


            date = posted_date_from_text(
                context
            )


            if not date:

                continue


            if url in seen:

                continue


            seen.add(
                url
            )


            title = notification_title(
                context
            )


            if len(title) > 300:

                title = (
                    title[:297]
                    + "..."
                )


            items.append(
                {
                    "title": title,

                    "url": url,

                    "date": date,

                    "description":
                        context[:800],

                    "source": source
                }
            )


    return items


# ============================================================
# LOAD EXISTING JSON
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
            "Could not read existing JSON:",
            error
        )

    return []


# ============================================================
# REMOVE DUPLICATES
# ============================================================

def deduplicate(items):

    unique = {}

    for item in items:

        url = clean(
            item.get(
                "url",
                ""
            )
        )

        if url:

            unique[url] = item

    return list(
        unique.values()
    )


# ============================================================
# SORT NEWEST FIRST
# ============================================================

def sort_key(item):

    return parse_datetime(
        item.get(
            "date",
            ""
        )
    )


# ============================================================
# MAIN
# ============================================================

def main():

    old_items = load_old()


    print()
    print("=" * 60)
    print("ANNA UNIVERSITY COE CHECK")
    print("=" * 60)


    # --------------------------------------------------------
    # Primary COE website
    # --------------------------------------------------------

    html = fetch(
        PRIMARY
    )

    source = PRIMARY

    items = []

    if html:

        items = parse_page(
            html,
            PRIMARY
        )


    # --------------------------------------------------------
    # Fallback website
    # --------------------------------------------------------

    if not items:

        print(
            "Primary source returned "
            "no notifications."
        )

        print(
            "Trying fallback..."
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

                source = FALLBACK


    # --------------------------------------------------------
    # SAFETY
    # --------------------------------------------------------
    #
    # NEVER erase the existing JSON if the COE website
    # temporarily fails.
    # --------------------------------------------------------

    if not items:

        print(
            "No notifications detected."
        )

        print(
            f"Keeping existing "
            f"{len(old_items)} notifications."
        )

        return


    # --------------------------------------------------------
    # Clean and sort
    # --------------------------------------------------------

    items = deduplicate(
        items
    )


    items.sort(
        key=sort_key,
        reverse=True
    )


    # Keep latest 200.
    items = items[:200]


    # --------------------------------------------------------
    # Save JSON
    # --------------------------------------------------------

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


    # --------------------------------------------------------
    # Find genuinely new notifications
    # --------------------------------------------------------

    old_urls = {

        clean(
            item.get(
                "url",
                ""
            )
        )

        for item in old_items

        if clean(
            item.get(
                "url",
                ""
            )
        )
    }


    new_items = [

        item

        for item in items

        if clean(
            item.get(
                "url",
                ""
            )
        )
        not in old_urls
    ]


    # --------------------------------------------------------
    # Result
    # --------------------------------------------------------

    print()
    print(
        f"Source: {source}"
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
        "Checked at:",
        datetime.now(
            timezone.utc
        ).isoformat()
    )

    print("=" * 60)


    for item in new_items[:20]:

        print(
            "NEW:",
            item.get(
                "date",
                ""
            ),
            "|",
            item.get(
                "title",
                ""
            )
        )

        print(
            "URL:",
            item.get(
                "url",
                ""
            )
        )


    print("=" * 60)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
