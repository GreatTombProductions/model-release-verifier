#!/usr/bin/env python3
"""Match tests — shared case table against the fixture index (Python reference)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from match import verify_claim  # noqa: E402

HERE = Path(__file__).resolve().parent
# Each case names its fixture: "aug" (Aug 2026 pages, default) or "oct" (Oct 2026 pages).
INDEXES = {
    "aug": json.load(open(HERE / "fixture_index.json", encoding="utf-8")),
    "oct": json.load(open(HERE / "fixture_index_oct.json", encoding="utf-8")),
}
CASES = json.load(open(HERE / "cases.json", encoding="utf-8"))["cases"]
# Shipped-index checks: source layout keeps the index at data/generated/, the
# published site at data/.
LIVE_INDEX = next(p for p in (HERE.parent / "data" / "generated" / "index.json", HERE.parent / "data" / "index.json") if p.exists())
INDEXES["live"] = json.load(open(LIVE_INDEX, encoding="utf-8"))
CASES += [{**c, "fixture": "live"} for c in json.load(open(HERE / "cases_live.json", encoding="utf-8"))["cases"]]


def main() -> None:
    failures = 0
    for c in CASES:
        got = verify_claim(c["claim"], INDEXES[c.get("fixture", "aug")])
        if got["verdict"] == c["verdict"]:
            print(f"OK   [{got['verdict']:>13}] {c['claim']}")
        else:
            failures += 1
            print(f"FAIL [{got['verdict']:>13} != {c['verdict']:>13}] {c['claim']}")
            print(f"      -> {got['reason'][:200]}")
    if failures:
        print(f"\n{failures} failure(s)")
        sys.exit(1)
    print(f"\nALL PASS ({len(CASES)} cases)")


if __name__ == "__main__":
    main()
