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

POSTED_DATE_TIME = re.compile(
    r"\b"
    r"\d{1,2}\s+"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"[a-z]*\s+\d{4}"
    r"\s+\d{1,2}:\d{2}\s*(?:AM|PM)"
    r"\b",
    re.I
)


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


def extract_posted_date(text):

    text = clean(text)

    # MOST IMPORTANT:
    # Prefer a date with a posting time.
    match = POSTED_DATE_TIME.search(
        text
    )

    if match:
        return clean(
            match.group(0)
        )

    # Fallback.
    match = ANY_DATE.search(
        text
    )

    if match:
        return clean(
            match.group(0)
        )

    return ""


# ============================================================
# FETCH
# ============================================================

def fetch(url):

    for attempt in range(3):

        try:

            print(
                f"Fetching {url} "
                f"(attempt {attempt + 1}/3)"
            )

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=45,
                verify=False,
                params={
                    "_": str(
                        int(time.time())
                    )
                }
            )

            response.raise_for_status()

            if len(response.text) < 500:
                raise RuntimeError(
                    "Response too small"
                )

            print(
                f"Fetched successfully: "
                f"{len(response.text)} bytes"
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
# FIND NOTIFICATION BOXES
# ============================================================

def find_boxes(soup):

    boxes = []

    seen = set()

    # Look for elements containing the actual
    # COE posting timestamp.
    for element in soup.find_all(
        ["tr", "li", "td", "div", "p", "article", "section"]
    ):

        text = clean(
            element.get_text(
                " ",
                strip=True
            )
        )

        if not text:
            continue

        if not POSTED_DATE_TIME.search(
            text
        ):
            continue

        # Ignore enormous page containers.
        if len(text) > 2000:
            continue

        # We want the smallest useful container.
        key = text[:1200]

        if key in seen:
            continue

        seen.add(key)

        boxes.append(
            element
        )

    # Smallest boxes first.
    boxes.sort(
        key=lambda element:
            len(
                clean(
                    element.get_text(
                        " ",
                        strip=True
                    )
                )
            )
    )

    return boxes


# ============================================================
# PARSE
# ============================================================

def parse_page(
    html,
    source
):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    for tag in soup.find_all(
        [
            "script",
            "style",
            "noscript"
        ]
    ):
        tag.decompose()

    results = {}

    boxes = find_boxes(
        soup
    )

    print(
        f"Timestamp containers found: "
        f"{len(boxes)}"
    )


    # ========================================================
    # Parse each timestamp-containing box
    # ========================================================

    for box in boxes:

        text = clean(
            box.get_text(
                " ",
                strip=True
            )
        )

        posted_date = (
            extract_posted_date(
                text
            )
        )

        if not posted_date:
            continue


        # ----------------------------------------------------
        # Find links anywhere inside this notification box.
        # ----------------------------------------------------

        links = box.find_all(
            "a",
            href=True
        )


        # If the timestamp box itself doesn't contain
        # the link, look slightly upward.
        if not links:

            parent = (
                box.find_parent("tr")
                or box.find_parent("li")
                or box.find_parent("td")
                or box.find_parent("div")
            )

            if parent:

                links = parent.find_all(
                    "a",
                    href=True
                )


        for link in links:

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


            # COE notification links are usually
            # documents or "Click Here".
            if not (
                is_document
                or is_click_here
            ):
                continue


            # ------------------------------------------------
            # TITLE
            # ------------------------------------------------

            title = re.sub(
                r"^\s*"
                + re.escape(
                    posted_date
                )
                + r"\s*",
                "",
                text,
                count=1,
                flags=re.I
            )


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
                title = link_text


            if not title:
                title = (
                    "Anna University "
                    "COE Notification"
                )


            if len(title) > 500:
                title = (
                    title[:497]
                    + "..."
                )


            # ------------------------------------------------
            # DESCRIPTION
            # ------------------------------------------------

            description = text

            if len(description) > 1000:
                description = (
                    description[:997]
                    + "..."
                )


            results[url] = {
                "title": title,
                "url": url,
                "date": posted_date,
                "description": description,
                "source": source
            }


    print(
        f"Notifications parsed: "
        f"{len(results)}"
    )

    return list(
        results.values()
    )


# ============================================================
# LOAD EXISTING
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

            data = json.load(
                file
            )

        if isinstance(
            data,
            list
        ):
            return data

    except Exception as error:

        print(
            f"JSON read error: {error}"
        )

    return []


# ============================================================
# MERGE
# ============================================================

def merge_notifications(
    old_items,
    new_items
):

    merged = {}

    # IMPORTANT:
    # Keep existing notifications first.
    for item in old_items:

        url = clean(
            item.get(
                "url",
                ""
            )
        )

        if url:
            merged[url] = item


    # New data replaces an existing URL,
    # but never deletes old URLs.
    for item in new_items:

        url = clean(
            item.get(
                "url",
                ""
            )
        )

        if url:
            merged[url] = item


    items = list(
        merged.values()
    )


    # Newest first.
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


    return items[:200]


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
    print("ANNA UNIVERSITY COE MONITOR")
    print("=" * 60)


    # --------------------------------------------------------
    # Primary
    # --------------------------------------------------------

    html = fetch(
        COE_URL
    )

    source = COE_URL

    new_items = []


    if html:

        new_items = parse_page(
            html,
            COE_URL
        )


    # --------------------------------------------------------
    # Fallback
    # --------------------------------------------------------

    if not new_items:

        print(
            "Primary parser found "
            "nothing."
        )

        print(
            "Trying fallback..."
        )

        fallback_html = fetch(
            FALLBACK_URL
        )

        if fallback_html:

            fallback_items = parse_page(
                fallback_html,
                FALLBACK_URL
            )

            if fallback_items:

                new_items = (
                    fallback_items
                )

                source = FALLBACK_URL


    # --------------------------------------------------------
    # SAFETY
    # --------------------------------------------------------

    if not new_items:

        print()
        print(
            "NO NEW DATA COULD BE PARSED."
        )

        print(
            f"Keeping existing "
            f"{len(old_items)} records."
        )

        print("=" * 60)

        return


    # --------------------------------------------------------
    # Merge instead of replacing
    # --------------------------------------------------------

    old_urls = {
        clean(
            item.get(
                "url",
                ""
            )
        )
        for item in old_items
    }


    merged = merge_notifications(
        old_items,
        new_items
    )


    # --------------------------------------------------------
    # Detect genuinely new URLs
    # --------------------------------------------------------

    genuinely_new = [

        item

        for item in new_items

        if clean(
            item.get(
                "url",
                ""
            )
        )
        not in old_urls
    ]


    save(
        merged
    )


    # --------------------------------------------------------
    # RESULT
    # --------------------------------------------------------

    print()
    print(
        f"Source: {source}"
    )

    print(
        f"Old records: {len(old_items)}"
    )

    print(
        f"Parsed records: {len(new_items)}"
    )

    print(
        f"Total records: {len(merged)}"
    )

    print(
        f"Genuinely new: "
        f"{len(genuinely_new)}"
    )

    print()
    print(
        "LATEST RECORDS:"
    )

    for item in merged[:10]:

        print(
            item.get(
                "date",
                ""
            ),
            "|",
            item.get(
                "title",
                ""
            )[:150]
        )

        print(
            item.get(
                "url",
                ""
            )
        )

        print("-" * 60)


    print(
        "CHECK COMPLETE"
    )

    print("=" * 60)


if __name__ == "__main__":
    main()
