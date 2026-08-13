#!/usr/bin/env python3
"""Fetch vendor semantic-authority pages with multi-transport + deadline discipline.

Patterns (S119/S121/S126, folded into DATA_SOURCE_PATTERNS.md):
  - Multi-transport fetch: urllib first, curl fallback. Transport divergence is
    real (platform.claude.com 404s via urllib while curl returns 200).
  - Per-source hard wall-clock deadline (thread + join timeout): a hanging
    server is a recorded failure, never a stall.
  - Fetch failure writes a flagged finding block, never a silent gap
    (honest-coverage rule — an absent vendor page is a documented gap).

Saves raw HTML to data/raw/ for offline parsing.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
FETCH_DEADLINE_SECONDS = 60

# Six sources across four vendors:
#   lifecycle pages (Anthropic, OpenAI) carry status/date records;
#   current-model pages (all four) carry the availability surface.
# DeepSeek and Kimi publish NO lifecycle/deprecation page — retirement claims
# against them are "unverifiable" with the gap documented (never assumed clean).
SOURCES = [
    ("anthropic_deprecations", "https://docs.claude.com/en/docs/about-claude/model-deprecations"),
    ("anthropic_models", "https://docs.claude.com/en/docs/about-claude/models/overview"),
    ("openai_deprecations", "https://developers.openai.com/api/docs/deprecations"),
    ("openai_models", "https://developers.openai.com/api/docs/models"),
    ("deepseek_docs", "https://api-docs.deepseek.com/"),
    ("kimi_platform", "https://platform.kimi.ai/"),
]


def _fetch_urllib(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _fetch_curl(url: str) -> bytes:
    proc = subprocess.run(
        ["curl", "-sL", "--max-time", "40", "-A", UA, url],
        capture_output=True, check=False, timeout=45,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"curl exit {proc.returncode}: {proc.stderr.decode()[:200]}")
    return proc.stdout


def fetch(url: str) -> bytes:
    """Fetch with urllib→curl fallback and a hard wall-clock deadline."""
    result: dict = {"ok": False, "data": None, "error": None}

    def work() -> None:
        try:
            result["data"] = _fetch_urllib(url)
            result["ok"] = True
        except Exception as exc:
            result["error"] = f"urllib: {exc}"
            try:
                result["data"] = _fetch_curl(url)
                result["ok"] = True
            except Exception as exc2:
                result["error"] = f"urllib: {exc}; curl: {exc2}"

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout=FETCH_DEADLINE_SECONDS)
    if t.is_alive():
        raise RuntimeError(f"timed out after {FETCH_DEADLINE_SECONDS}s (hanging server)")
    if not result["ok"]:
        raise RuntimeError(result["error"])
    return result["data"]


def main() -> None:
    raw = Path(__file__).resolve().parent.parent / "data" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    failures = []
    for name, url in SOURCES:
        try:
            body = fetch(url)
            out = raw / f"{name}-{ts}.html"
            out.write_bytes(body)
            print(f"OK   {name}: {len(body)} bytes -> {out.name}")
        except Exception as exc:
            failures.append((name, str(exc)))
            print(f"FAIL {name}: {exc}", file=sys.stderr)
    if failures:
        # Flagged, never silent — build.py re-reads this file and documents
        # each failure as a coverage gap in sources.json.
        log = raw / "fetch-failures.txt"
        with open(log, "w", encoding="utf-8") as f:
            for name, err in failures:
                f.write(f"{ts} {name} {err}\n")
        print(f"Wrote {len(failures)} failure(s) to {log}", file=sys.stderr)


if __name__ == "__main__":
    main()
