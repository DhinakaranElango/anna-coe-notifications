import json
import re
import time
import urllib3

from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


# ============================================================
# CONFIG
# ============================================================

ROOT = Path(__file__).resolve().parent

OUTPUT = ROOT / "data" / "notifications.json"

COE_URL = "https://coe.annauniv.edu/home/index.php"

FALLBACK_URL = "https://aucoe.annauniv.edu/"


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


urllib3.disable_warnings(
    urllib3.exceptions.InsecureRequestWarning
)


# ============================================================
# DATE PATTERNS
# ============================================================

# Example:
# 19 Sep 2026 03:13 PM

POSTED_DATE_TIME = re.compile(
    r"\b"
    r"\d{1,2}\s+"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"[a-z]*\s+\d{4}"
    r"\s+\d{1,2}:\d{2}\s*(?:AM|PM)"
    r"\b",
    re.I
)


# Examples:
# 19-09-2026
# 19/09/2026
# 19 Sep 2026

ANY_DATE = re.compile(
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
    re.I
)


# ============================================================
# HELPERS
# ============================================================

def clean(text):
    return " ".join(
        str(text or "").split()
    )


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
            return datetime.strptime(
                value,
                fmt
            )
        except ValueError:
            pass

    return datetime.min


def get_posted_date(text):
    """
    IMPORTANT:

    If the notification contains:

        19 Sep 2026 03:13 PM

    and later:

        21-09-2026

    the first value is used as the notification date.
    """

    text = clean(text)

    match = POSTED_DATE_TIME.search(
        text
    )

    if match:
        return match.group(0)

    match = ANY_DATE.search(
        text
    )

    if match:
        return match.group(0)

    return ""


# ============================================================
# FETCH
# ============================================================

def fetch(url):

    for attempt in range(3):

        try:

            print(
                f"Fetching: {url} "
                f"[attempt {attempt + 1}/3]"
            )

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=45,
                verify=False,
                params={
                    "_": int(time.time())
                }
            )

            response.raise_for_status()

            if len(response.text) < 500:
                raise RuntimeError(
                    "Website returned very little content"
                )

            return response.text

        except Exception as error:

            print(
                f"Fetch error: {error}"
            )

            if attempt < 2:
                time.sleep(2)

    return None


# ============================================================
# GET NOTIFICATION CONTAINER
# ============================================================

def get_container(link):

    """
    Find the smallest surrounding element containing
    the COE posting date/time.

    This prevents a large page container from causing
    the wrong date to be selected.
    """

    candidates = []

    for tag in [
        "tr",
        "li",
        "td",
        "p",
        "div",
    ]:

        parent = link.find_parent(tag)

        if not parent:
            continue

        text = clean(
            parent.get_text(
                " ",
                strip=True
            )
        )

        if POSTED_DATE_TIME.search(text):

            candidates.append(
                (
                    len(text),
                    parent
                )
            )

    if candidates:

        candidates.sort(
            key=lambda x: x[0]
        )

        return candidates[0][1]

    return None


# ============================================================
# TITLE
# ============================================================

def make_title(text):

    text = clean(text)

    # Remove posting date/time at the beginning.
    text = POSTED_DATE_TIME.sub(
        "",
        text,
        count=1
    )

    # Remove "Click Here".
    text = re.sub(
        r"\s*click\s*here\s*$",
        "",
        text,
        flags=re.I
    )

    text = clean(text)

    if not text:
        text = "Anna University COE Notification"

    if len(text) > 300:
        text = text[:297] + "..."

    return text


# ============================================================
# PARSER
# ============================================================

def parse_page(html, source):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    # Remove scripts/styles.
    for tag in soup.find_all(
        [
            "script",
            "style",
            "noscript"
        ]
    ):
        tag.decompose()

    notifications = []

    seen = set()


    # --------------------------------------------------------
    # COE notification links
    # --------------------------------------------------------

    for link in soup.find_all(
        "a",
        href=True
    ):

        href = clean(
            link.get(
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
            link.get_text(
                " ",
                strip=True
            )
        )


        container = get_container(
            link
        )


        if container:

            text = clean(
                container.get_text(
                    " ",
                    strip=True
                )
            )

        else:

            # Try the closest common notification container.
            parent = (
                link.find_parent("tr")
                or link.find_parent("li")
                or link.find_parent("p")
            )

            if parent:

                text = clean(
                    parent.get_text(
                        " ",
                        strip=True
                    )
                )

            else:

                text = link_text


        # We only want links that look like
        # actual notifications/documents.
        is_document = bool(
            re.search(
                r"\.(pdf|doc|docx|xls|xlsx)"
                r"(?:[?#].*)?$",
                url,
                re.I
            )
        )


        is_click_here = (
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
            or is_click_here
        ):
            continue


        # ----------------------------------------------------
        # POSTED DATE
        # ----------------------------------------------------

        date = get_posted_date(
            text
        )


        if not date:
            continue


        # ----------------------------------------------------
        # DUPLICATE
        # ----------------------------------------------------

        if url in seen:
            continue

        seen.add(url)


        # ----------------------------------------------------
        # TITLE
        # ----------------------------------------------------

        title = make_title(
            text
        )


        # ----------------------------------------------------
        # DESCRIPTION
        # ----------------------------------------------------

        description = text

        if len(description) > 800:
            description = (
                description[:797]
                + "..."
            )


        notifications.append(
            {
                "title": title,
                "url": url,
                "date": date,
                "description": description,
                "source": source
            }
        )


    return notifications


# ============================================================
# LOAD OLD DATA
# ============================================================

def load_existing():

    if not OUTPUT.exists():
        return []

    try:

        with open(
            OUTPUT,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        if isinstance(data, list):
            return data

    except Exception as error:

        print(
            f"Could not load old data: {error}"
        )

    return []


# ============================================================
# SAVE
# ============================================================

def save(items):

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(
        OUTPUT,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            items,
            file,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# MAIN
# ============================================================

def main():

    old_items = load_existing()

    print()
    print("=" * 60)
    print("ANNA UNIVERSITY COE NOTIFICATION CHECK")
    print("=" * 60)


    # --------------------------------------------------------
    # Try primary
    # --------------------------------------------------------

    html = fetch(
        COE_URL
    )

    source = COE_URL

    items = []


    if html:

        items = parse_page(
            html,
            COE_URL
        )


    # --------------------------------------------------------
    # Try fallback
    # --------------------------------------------------------

    if not items:

        print(
            "Primary parser returned no notifications."
        )

        print(
            "Trying fallback..."
        )


        html = fetch(
            FALLBACK_URL
        )


        if html:

            items = parse_page(
                html,
                FALLBACK_URL
            )

            if items:
                source = FALLBACK_URL


    # --------------------------------------------------------
    # NEVER erase good data
    # --------------------------------------------------------

    if not items:

        print(
            "No notifications found."
        )

        print(
            "Keeping existing database."
        )

        return


    # --------------------------------------------------------
    # Remove duplicates
    # --------------------------------------------------------

    unique = {}

    for item in items:

        url = item.get(
            "url",
            ""
        )

        if url:
            unique[url] = item


    items = list(
        unique.values()
    )


    # --------------------------------------------------------
    # Newest first
    # --------------------------------------------------------

    items.sort(
        key=lambda item:
            parse_date(
                item.get(
                    "date",
                    ""
                )
            ),
        reverse=True
    )


    # --------------------------------------------------------
    # Keep latest 200
    # --------------------------------------------------------

    items = items[:200]


    # --------------------------------------------------------
    # Detect genuinely new items
    # --------------------------------------------------------

    old_urls = {

        item.get(
            "url",
            ""
        )

        for item in old_items
    }


    new_items = [

        item

        for item in items

        if item.get(
            "url",
            ""
        )
        not in old_urls
    ]


    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------

    save(
        items
    )


    # --------------------------------------------------------
    # RESULT
    # --------------------------------------------------------

    print()
    print(
        f"Source: {source}"
    )

    print(
        f"Notifications found: {len(items)}"
    )

    print(
        f"New notifications: {len(new_items)}"
    )

    print("=" * 60)


    for item in new_items[:10]:

        print(
            "NEW:",
            item.get(
                "date",
                ""
            )
        )

        print(
            item.get(
                "title",
                ""
            )
        )

        print(
            item.get(
                "url",
                ""
            )
        )

        print("-" * 60)


if __name__ == "__main__":
    main()
