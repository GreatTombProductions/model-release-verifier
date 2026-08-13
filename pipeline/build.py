#!/usr/bin/env python3
"""
build.py — Assemble the static model index.

1. Ensure raw vendor pages exist (fetch if missing; reuse the newest fetch).
2. Parse each surface with parser.py.
3. Merge + dedupe into data/generated/index.json:
     {v, built, models[], aliases{}, coverage{}}
   coverage carries per-vendor surface status AND documented gaps (a vendor
   with no lifecycle page is a gap, never silently clean — S119).

The frontend consumes index.json directly; no build-time client codegen.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
PIPELINE = PROJECT / "pipeline"
RAW = PROJECT / "data" / "raw"
GEN = PROJECT / "data" / "generated"

sys.path.insert(0, str(PIPELINE))
import fetch as fetch_mod   # noqa: E402
import parser as parser_mod  # noqa: E402

# source name -> (vendor, surface kind, url)
SOURCE_META = {
    "anthropic_deprecations": ("Anthropic", "lifecycle"),
    "anthropic_models": ("Anthropic", "models"),
    "openai_deprecations": ("OpenAI", "lifecycle"),
    "openai_models": ("OpenAI", "models"),
    "deepseek_docs": ("DeepSeek", "models"),
    "kimi_platform": ("Kimi", "models"),
}


def _latest_raw(name: str) -> Path | None:
    files = sorted(RAW.glob(f"{name}-*.html"))
    return files[-1] if files else None


def _ensure_raw() -> None:
    """Fetch any missing source. Existing fetches are reused (the build is
    deterministic from the newest snapshot)."""
    missing = [n for n in SOURCE_META if _latest_raw(n) is None]
    if not missing:
        return
    print(f"Fetching {len(missing)} missing source(s)...", file=sys.stderr)
    fetch_mod.SOURCES = [(n, u) for n, u in fetch_mod.SOURCES if n in missing]
    fetch_mod.main()


def _read_failures() -> dict:
    """Fetch failures recorded by fetch.py -> per-source gap entries."""
    f = RAW / "fetch-failures.txt"
    out = {}
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            parts = line.split(" ", 2)
            if len(parts) >= 2:
                out[parts[1]] = parts[2] if len(parts) > 2 else "fetch failed"
    return out


def _anthropic_aliases() -> dict:
    """alias id -> canonical id from the overview page's 'Claude API alias' row."""
    f = _latest_raw("anthropic_models")
    if not f:
        return {}
    html = f.read_text(encoding="utf-8")
    aliases: dict = {}
    for tbl in re.findall(r"<table[^>]*>(.*?)</table>", html, flags=re.S):
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, flags=re.S)
        canon: list[str] = []
        alias_row = None
        for row in rows:
            cells = [parser_mod._strip_tags(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, flags=re.S)]
            if not cells:
                continue
            if cells[0] == "Claude API ID":
                canon = [c.split(" ")[0] for c in cells[1:]]
            elif cells[0] == "Claude API alias":
                alias_row = cells[1:]
        if canon and alias_row and len(canon) == len(alias_row):
            for a, c in zip(alias_row, canon):
                aliases[a.split(" ")[0].lower()] = c.lower()
            break
    return aliases


def main() -> None:
    _ensure_raw()
    failures = _read_failures()
    GEN.mkdir(parents=True, exist_ok=True)

    models: list[dict] = []
    coverage: dict = {}
    for name, (vendor, kind) in SOURCE_META.items():
        url = dict(fetch_mod.SOURCES)[name]
        f = _latest_raw(name)
        cov = coverage.setdefault(vendor, {
            "vendor": vendor,
            "has_lifecycle_page": False,
            "surfaces": {},
            "gaps": [],
        })
        if kind == "lifecycle":
            cov["has_lifecycle_page"] = True
        if f is None or name in failures:
            cov["surfaces"][name] = {"url": url, "status": "unreachable", "error": failures.get(name, "no fetch on record")}
            cov["gaps"].append(f"{name} unreachable at build time ({failures.get(name, 'no fetch on record')})")
            continue
        html = f.read_text(encoding="utf-8")
        fetched_at = f.name.split("-", 1)[1].removesuffix(".html")  # YYYYMMDDTHHMMSSZ
        records = parser_mod.PARSERS[name](html, fetched_at=fetched_at)
        models.extend(records)
        cov["surfaces"][name] = {"url": url, "status": "ok", "records": len(records), "fetched_at": fetched_at}
        print(f"  {name}: {len(records)} records ({fetched_at})")

    # per-vendor documented gaps (absence of a page is a gap, not clean)
    for vendor, cov in coverage.items():
        if not cov["has_lifecycle_page"]:
            cov["gaps"].append(
                f"{vendor} publishes no model-deprecation/lifecycle page — "
                f"retirement claims against {vendor} are unverifiable, not clean."
            )

    # dedupe WITHIN kind: a model with both a current listing and a lifecycle
    # record keeps both — the availability verdict quotes the models page, the
    # retirement verdict quotes the deprecations page. Only exact same-surface
    # duplicates collapse.
    merged: dict = {}
    for rec in models:
        key = (rec["vendor"], rec["model_id"].lower(), rec["kind"])
        if key not in merged or (rec["kind"] == "lifecycle" and merged[key]["kind"] != "lifecycle"):
            merged[key] = rec
    models = list(merged.values())
    models.sort(key=lambda r: (r["vendor"], r["model_id"]))

    index = {
        "v": 1,
        "built": date.today().isoformat(),
        "models": models,
        "aliases": _anthropic_aliases(),
        # claim-side short forms, gated on the vendor keyword appearing in the
        # claim ("V4 Flash" only resolves to deepseek-v4-flash when the claim
        # also says "deepseek"). Consumed by BOTH match.py and the JS mirror.
        "short_aliases": {
            "v4 flash": ["DeepSeek", "deepseek-v4-flash"],
            "v4-flash": ["DeepSeek", "deepseek-v4-flash"],
            "v4 pro": ["DeepSeek", "deepseek-v4-pro"],
            "v4-pro": ["DeepSeek", "deepseek-v4-pro"],
            "k3": ["Kimi", "kimi-k3"],
            "k2 thinking": ["Kimi", "kimi-k2-thinking"],
            "k2 turbo": ["Kimi", "kimi-k2-turbo-preview"],
        },
        "coverage": coverage,
        "methodology": {
            "verdicts": [
                "confirmed — the vendor's own page supports the claim",
                "contradicted — the vendor's page shows a different state, or no such model",
                "partial — one part confirmed, another unverifiable (e.g. existence yes, release date has no vendor surface)",
                "unverifiable — no vendor surface exists for this claim class (documented gap)",
            ],
            "sources": [dict(fetch_mod.SOURCES)[n] for n in SOURCE_META],
            "note": (
                "Vendors publish deprecation/retirement surfaces for some models "
                "and current-model listings for others; neither publishes release "
                "dates for most models. Every verdict quotes the vendor page it "
                "rests on, with the fetch timestamp."
            ),
        },
    }
    out = GEN / "index.json"
    out.write_text(json.dumps(index, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    size = out.stat().st_size
    n = len(models)
    print(f"\nindex.json: {n} models, {size/1e3:.1f} KB -> {out}")


if __name__ == "__main__":
    main()
