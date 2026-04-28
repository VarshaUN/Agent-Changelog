import json
import os
import sqlite3
from scraper import init_db, get_all_diffs, DB_PATH
import requests

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

CLASSIFY_PROMPT = """You are an API changelog analyst for AI agents.
Given new lines from an API changelog diff, classify each change and return JSON only.

Output format (array, one object per meaningful change):
[
  {
    "type": "breaking_change | new_endpoint | deprecation | parameter_change | bug_fix | docs_fix | other",
    "summary": "one sentence, agent-readable",
    "affected": "endpoint or feature name, or null",
    "severity": "high | medium | low",
    "raw": "the original line"
  }
]

Changelog diff to classify:
{diff_text}

Return only valid JSON. No explanation, no markdown fences."""


def classify_diff(new_items: list[str]) -> list[dict]:
    if not new_items:
        return []

    diff_text = "\n".join(new_items[:50])

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5-20251001",  # cheapest, fast enough
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": CLASSIFY_PROMPT.format(diff_text=diff_text)}],
        },
        timeout=30,
    )
    response.raise_for_status()
    text = response.json()["content"][0]["text"].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)


def run_classifier():
    conn = init_db()

    # Get diffs that haven't been classified yet and have new_items
    rows = conn.execute(
        "SELECT id, api, new_items FROM diffs WHERE is_first_run = 0 ORDER BY id DESC LIMIT 20"
    ).fetchall()

    if not rows:
        print("No unclassified diffs found. Run scraper.py first and wait for a change.")
        return

    for row_id, api, new_items_json in rows:
        new_items = json.loads(new_items_json)
        if not new_items:
            continue

        print(f"Classifying diff id={row_id} api={api} ({len(new_items)} new lines)…")
        classifications = classify_diff(new_items)

        print(json.dumps(classifications, indent=2))
        print()

    conn.close()


if __name__ == "__main__":
    run_classifier()