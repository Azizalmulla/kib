"""Entry point: python -m scripts.scraper [kib|cbk|all]"""

import sys

from .scrape_kib import run as run_kib
from .scrape_cbk import run as run_cbk


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    target = target.lower()

    summaries = []

    if target in ("kib", "all"):
        summaries.append(run_kib())

    if target in ("cbk", "all"):
        summaries.append(run_cbk())

    if not summaries:
        print(f"Unknown target: {target}. Use: kib, cbk, or all")
        return 1

    print("\n" + "=" * 60)
    print("FINAL SUMMARY")
    print("=" * 60)
    total_ingested = 0
    for s in summaries:
        print(f"  {s['site']}: {s['ingested']} ingested / {s['urls_discovered']} discovered / {s['skipped']} skipped / {s['errors']} errors")
        total_ingested += s["ingested"]

    print(f"\n  Total documents ingested: {total_ingested}")
    print("=" * 60)

    return 0 if total_ingested > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
