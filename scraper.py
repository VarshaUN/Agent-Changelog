import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup, Tag

DB_PATH = Path(__file__).parent / "changelog.db"
HEADERS  = {"User-Agent": "AgentChangelog/1.0 (+https://github.com/you/agent-changelog)"}
TIMEOUT  = 15

APIS: list[dict] = [
    {
        "name":     "stripe",
        "url":      "https://stripe.com/docs/changelog",
        "selector": "article",
        "notes":    "Stripe changelog — one article per release date",
    },
    {
    "name":     "openai",
    "url":      "https://raw.githubusercontent.com/openai/openai-python/main/CHANGELOG.md",
    "selector": "",
    "notes":    "OpenAI Python SDK changelog on GitHub",
    },
    {
        "name":     "anthropic",
        "url":      "https://docs.anthropic.com/en/release-notes/api",
        "selector": "h2, h3",
        "notes":    "Anthropic API release notes",
    },
    {
    "name":     "twilio",
    "url":      "https://www.twilio.com/en-us/changelog",
    "selector": "article",
    "notes":    "Twilio product changelog",
    },
    {
    "name":     "github",
    "url":      "https://raw.githubusercontent.com/github/rest-api-description/main/CHANGELOG.md",
    "selector": "",
    "notes":    "GitHub REST API changelog",
   },
   {
    "name":     "langchain",
    "url":      "https://raw.githubusercontent.com/langchain-ai/langchain/master/CHANGELOG.md",
    "selector": "",
    "notes":    "LangChain changelog",
   },
]

@dataclass
class ChangelogEntry:
    api:          str
    detected_at:  str
    content_hash: str
    raw_text:     str
    new_items:    list[str]
    removed_items: list[str]
    is_first_run: bool

def init_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Create tables if they don't exist yet."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            api          TEXT    NOT NULL,
            captured_at  TEXT    NOT NULL,
            content_hash TEXT    NOT NULL,
            raw_text     TEXT    NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS diffs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            api          TEXT    NOT NULL,
            detected_at  TEXT    NOT NULL,
            content_hash TEXT    NOT NULL,
            new_items    TEXT    NOT NULL,   -- JSON array
            removed_items TEXT   NOT NULL,   -- JSON array
            raw_text     TEXT    NOT NULL,
            is_first_run INTEGER NOT NULL
        )
    """)
    conn.commit()
    return conn


def get_last_snapshot(conn: sqlite3.Connection, api: str) -> Optional[dict]:
    """Return the most recent snapshot for this API, or None."""
    row = conn.execute(
        "SELECT raw_text, content_hash FROM snapshots WHERE api = ? ORDER BY id DESC LIMIT 1",
        (api,)
    ).fetchone()
    if row:
        return {"raw_text": row[0], "content_hash": row[1]}
    return None


def save_snapshot(conn: sqlite3.Connection, api: str, text: str, content_hash: str) -> None:
    conn.execute(
        "INSERT INTO snapshots (api, captured_at, content_hash, raw_text) VALUES (?, ?, ?, ?)",
        (api, now_utc(), content_hash, text)
    )
    conn.commit()


def save_diff(conn: sqlite3.Connection, entry: ChangelogEntry) -> None:
    conn.execute(
        """INSERT INTO diffs
           (api, detected_at, content_hash, new_items, removed_items, raw_text, is_first_run)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            entry.api,
            entry.detected_at,
            entry.content_hash,
            json.dumps(entry.new_items),
            json.dumps(entry.removed_items),
            entry.raw_text,
            int(entry.is_first_run),
        )
    )
    conn.commit()


def get_all_diffs(conn: sqlite3.Connection, api: Optional[str] = None) -> list[dict]:
    """Retrieve stored diffs, optionally filtered by API name."""
    if api:
        rows = conn.execute(
            "SELECT * FROM diffs WHERE api = ? ORDER BY id DESC", (api,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM diffs ORDER BY id DESC").fetchall()

    cols = ["id", "api", "detected_at", "content_hash",
            "new_items", "removed_items", "raw_text", "is_first_run"]
    results = []
    for row in rows:
        d = dict(zip(cols, row))
        d["new_items"]     = json.loads(d["new_items"])
        d["removed_items"] = json.loads(d["removed_items"])
        d["is_first_run"]  = bool(d["is_first_run"])
        results.append(d)
    return results

def fetch_page(url: str) -> Optional[str]:
    """Fetch a URL and return raw HTML, or None on failure."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        print(f"  [fetch error] {url}: {e}")
        return None


def extract_text(html: str, selector: str) -> str:
    """
    Extract and clean text from the page.
    Tries the CSS selector first; falls back to full-page body text.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "head", "noscript", "svg"]):
        tag.decompose()

    elements = soup.select(selector)

    if elements:
        parts = [el.get_text(separator=" ", strip=True) for el in elements]
        text  = "\n\n".join(parts)
    else:
        body = soup.find("body") or soup
        text = body.get_text(separator="\n", strip=True)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()

def line_set(text: str) -> set[str]:
    """Split text into a set of non-empty, stripped lines."""
    return {line.strip() for line in text.splitlines() if line.strip()}


def diff_texts(old_text: str, new_text: str) -> tuple[list[str], list[str]]:
    """
    Return (new_items, removed_items) — lines that appeared or vanished.
    Lines are sorted for deterministic output.
    """
    old_lines = line_set(old_text)
    new_lines = line_set(new_text)

    added   = sorted(new_lines - old_lines)
    removed = sorted(old_lines - new_lines)
    return added, removed

def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def process_api(conn: sqlite3.Connection, api_cfg: dict) -> Optional[ChangelogEntry]:
    """
    Scrape one API changelog, diff against last snapshot.
    Returns a ChangelogEntry if something changed (or on first run), else None.
    """
    name     = api_cfg["name"]
    url      = api_cfg["url"]
    selector = api_cfg["selector"]

    print(f"  Fetching {name} …")
    html = fetch_page(url)
    if html is None:
        return None

    text  = extract_text(html, selector)
    chash = hash_text(text)
    last  = get_last_snapshot(conn, name)

    if last is None:
        print(f"  [{name}] First run — saving baseline snapshot.")
        save_snapshot(conn, name, text, chash)
        entry = ChangelogEntry(
            api=name,
            detected_at=now_utc(),
            content_hash=chash,
            raw_text=text,
            new_items=[],
            removed_items=[],
            is_first_run=True,
        )
        save_diff(conn, entry)
        return entry

    if chash == last["content_hash"]:
        print(f"  [{name}] No change (hash match). Skipping.")
        return None

    added, removed = diff_texts(last["raw_text"], text)
    print(f"  [{name}] Change detected! +{len(added)} lines, -{len(removed)} lines.")
    save_snapshot(conn, name, text, chash)
    entry = ChangelogEntry(
        api=name,
        detected_at=now_utc(),
        content_hash=chash,
        raw_text=text,
        new_items=added,
        removed_items=removed,
        is_first_run=False,
    )
    save_diff(conn, entry)
    return entry


def run_scraper(apis: list[dict] = APIS) -> list[ChangelogEntry]:
    """Main entrypoint. Scrape all APIs, return entries that changed."""
    print(f"\n{'='*50}")
    print(f"Agent Changelog — {now_utc()}")
    print(f"{'='*50}")

    conn    = init_db()
    changed = []

    for api_cfg in apis:
        entry = process_api(conn, api_cfg)
        if entry:
            changed.append(entry)
        time.sleep(1)

    conn.close()

    print(f"\nDone. {len(changed)} API(s) had changes this run.")
    return changed

def print_summary(entries: list[ChangelogEntry]) -> None:
    if not entries:
        print("Nothing new.")
        return
    for e in entries:
        print(f"\n{'─'*40}")
        print(f"API:          {e.api}")
        print(f"Detected at:  {e.detected_at}")
        print(f"Hash:         {e.content_hash[:16]}…")
        if e.is_first_run:
            print("Status:       First run — baseline saved.")
        else:
            print(f"New lines:    {len(e.new_items)}")
            if e.new_items:
                for line in e.new_items[:5]:
                    print(f"  + {line[:120]}")
                if len(e.new_items) > 5:
                    print(f"  … and {len(e.new_items) - 5} more")
            print(f"Removed lines: {len(e.removed_items)}")


if __name__ == "__main__":
    results = run_scraper()
    print_summary(results)
