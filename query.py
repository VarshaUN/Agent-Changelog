"""
Agent Changelog — query.py
Read and inspect diffs stored by scraper.py.

Usage:
  python query.py                   # show all diffs
  python query.py --api stripe      # filter by API
  python query.py --latest          # most recent diff per API
  python query.py --json            # output raw JSON
"""

import argparse
import json
from scraper import init_db, get_all_diffs


def main():
    parser = argparse.ArgumentParser(description="Query Agent Changelog diffs")
    parser.add_argument("--api",    help="Filter by API name (stripe, openai, anthropic)")
    parser.add_argument("--latest", action="store_true", help="Show only most recent diff per API")
    parser.add_argument("--json",   action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    conn  = init_db()
    diffs = get_all_diffs(conn, api=args.api)
    conn.close()

    if not diffs:
        print("No diffs found. Run scraper.py first.")
        return

    if args.latest:
        seen  = set()
        dedup = []
        for d in diffs:
            if d["api"] not in seen:
                seen.add(d["api"])
                dedup.append(d)
        diffs = dedup

    if args.json:
        out = [{k: v for k, v in d.items() if k != "raw_text"} for d in diffs]
        print(json.dumps(out, indent=2))
        return

    for d in diffs:
        print(f"\n{'='*50}")
        print(f"ID:           {d['id']}")
        print(f"API:          {d['api']}")
        print(f"Detected at:  {d['detected_at']}")
        print(f"Hash:         {d['content_hash'][:16]}…")
        if d["is_first_run"]:
            print("Status:       First run baseline")
        else:
            print(f"New lines:    {len(d['new_items'])}")
            for line in d["new_items"][:5]:
                print(f"  + {line[:120]}")
            if len(d["new_items"]) > 5:
                print(f"  … and {len(d['new_items']) - 5} more")
            print(f"Removed:      {len(d['removed_items'])}")
            for line in d["removed_items"][:3]:
                print(f"  - {line[:120]}")


if __name__ == "__main__":
    main()
