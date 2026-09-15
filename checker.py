import json
import hashlib
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://coe.annauniv.edu/home/index.php"

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

OUTPUT = DATA_DIR / "notifications.json"

KEYWORDS = (
    "notification",
    "circular",
    "timetable",
    "exam",
    "result",
    "hall",
    "ticket",
    "revaluation",
    "semester",
    "schedule",
    "fee",
    "registration",
)

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 AnnaUniversityCOENotifier/1.0"
})

try:
    response = session.get(BASE_URL, timeout=30)
    response.raise_for_status()
except requests.RequestException as error:
    print("COE website could not be reached.")
    print(error)
    print("Keeping the existing notification data.")
    raise SystemExit(0)

soup = BeautifulSoup(response.text, "html.parser")

notifications = []
seen = set()

for link in soup.find_all("a", href=True):
    title = " ".join(link.get_text(" ", strip=True).split())
    href = link.get("href", "").strip()

    if not title or len(title) < 4:
        continue

    if href.startswith(("javascript:", "mailto:", "#")):
        continue

    url = urljoin(BASE_URL, href)

    parent_text = ""
    if link.parent:
        parent_text = " ".join(
            link.parent.get_text(" ", strip=True).split()
        )

    searchable = f"{title} {parent_text} {url}".lower()

    if not any(keyword in searchable for keyword in KEYWORDS):
        continue

    key = (title.lower(), url)

    if key in seen:
        continue

    seen.add(key)

    notifications.append({
        "title": title,
        "url": url,
        "date": "",
        "source": BASE_URL
    })

notifications = notifications[:150]

old = []

if OUTPUT.exists():
    try:
        old = json.loads(OUTPUT.read_text(encoding="utf-8"))
    except Exception:
        old = []

old_keys = {
    (item.get("title", ""), item.get("url", ""))
    for item in old
}

new_items = [
    item for item in notifications
    if (item["title"], item["url"]) not in old_keys
]

fingerprint = hashlib.sha256(
    json.dumps(
        notifications,
        ensure_ascii=False,
        sort_keys=True
    ).encode("utf-8")
).hexdigest()

OUTPUT.write_text(
    json.dumps(
        notifications,
        ensure_ascii=False,
        indent=2
    ),
    encoding="utf-8"
)

print(f"Found {len(notifications)} notifications.")
print(f"New notifications: {len(new_items)}")
print(f"Fingerprint: {fingerprint}")

for item in new_items:
    print("NEW:", item["title"], item["url"])
