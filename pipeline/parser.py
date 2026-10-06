#!/usr/bin/env python3
"""
parser.py — Parse vendor semantic-authority pages into a normalized model index.

Four vendors, six surfaces:
  Anthropic deprecations   -> lifecycle records (status, announced, retirement, replacement)
  Anthropic models overview-> current model ids (the availability surface)
  OpenAI deprecations      -> lifecycle records
  OpenAI models            -> current model ids
  DeepSeek api-docs        -> current model ids + version-update notes (NO lifecycle page)
  Kimi platform            -> current model ids via quickstart doc links (NO lifecycle page)

The Anthropic/OpenAI deprecation-page parsers are ported from the Notice-Policy
Check pipeline (projects/notice-policy/pipeline/parser.py, S126) — same page
structure, same traps (h3-id announcement dates, mixed US/ISO date formats,
whitespace-fragment model cells, API/system row exclusions).

Pure functions (HTML string in -> records out) so tests run offline.

Index record shape:
{
  "vendor": "Anthropic" | "OpenAI" | "DeepSeek" | "Kimi",
  "model_id": "claude-opus-4-1-20250805",
  "family": "Claude Opus 4.1",             # human-readable name or None
  "kind": "current" | "lifecycle",          # which surface it came from
  "status": "active" | "deprecated" | "retired" | "listed",
  "announced": "2026-06-05" | null,
  "retirement": "2026-08-05" | null,
  "replacement": "..." | null,
  "source_url": "...",
  "evidence": "short verbatim-ish quote from the page",
  "fetched_at": "2026-08-13T02:00:00Z",
}
"""
from __future__ import annotations

import html as html_mod
import re
from datetime import date, datetime, timezone
from typing import List, Optional

MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12,
}
MONTHS_ABBR = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

ANTHROPIC_DEPRECATIONS_URL = "https://docs.claude.com/en/docs/about-claude/model-deprecations"
ANTHROPIC_MODELS_URL = "https://docs.claude.com/en/docs/about-claude/models/overview"
OPENAI_DEPRECATIONS_URL = "https://developers.openai.com/api/docs/deprecations"
OPENAI_MODELS_URL = "https://developers.openai.com/api/docs/models"
DEEPSEEK_DOCS_URL = "https://api-docs.deepseek.com/"
KIMI_PLATFORM_URL = "https://platform.kimi.ai/"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _strip_tags(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    s = html_mod.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _text(html: str) -> str:
    s = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    s = re.sub(r"<style.*?</style>", " ", s, flags=re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html_mod.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _parse_us_date(s: str) -> Optional[str]:
    """'August 5, 2026' / 'Aug 5, 2026' / ISO '2026-02-17' -> '2026-02-17'.

    Both formats must parse (S126 trap: the original parser accepted only
    US-form and silently dropped every ISO-dated retirement)."""
    if not s:
        return None
    s = s.strip()
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),?\s*(\d{4})", s)
    if m:
        mon = MONTHS.get(m.group(1)) or MONTHS_ABBR.get(m.group(1)[:3])
        if mon is not None:
            return f"{m.group(3)}-{mon:02d}-{int(m.group(2)):02d}"
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)
    return None


def _today_iso() -> str:
    return date.today().isoformat()


def _fetched_at() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_display(name: str) -> str:
    """'GPT-6.1 Sol' -> 'gpt-6.1-sol' (display name to id form)."""
    return re.sub(r"\s+", "-", name.strip().lower())


def _family_from_claude_id(mid: str) -> str:
    m = re.match(r"claude-(.+)-(\d{8})$", mid)
    if m:
        base = re.sub(r"\b(\d+)\s+(\d+)\b", r"\1.\2", m.group(1).replace("-", " "))
        return f"Claude {base.title()}"
    m = re.match(r"claude-(.+)$", mid)
    if m:
        # claude-opus-5-5 -> Claude Opus 5.5 (same digit join as dated ids)
        base = re.sub(r"\b(\d+)\s+(\d+)\b", r"\1.\2", m.group(1).replace("-", " "))
        return "Claude " + base.title()
    return mid


# ---------------------------------------------------------------------------
# Anthropic — deprecations page (lifecycle records)
# ---------------------------------------------------------------------------

def _parse_not_sooner(s: str) -> Optional[str]:
    if not s or "Not sooner than" not in s:
        return None
    return _parse_us_date(s.replace("Not sooner than", ""))


def _parse_anthropic_history(html: str) -> List[dict]:
    """History sections: <h3> with nested div id="YYYY-MM-DD-<slug>" (the
    announcement date) + a replacement table per section."""
    records: List[dict] = []
    # Two layouts: Aug 2026 nests the dated id in a div inside the <h3>;
    # Oct 2026 puts it on the <h3> itself. A section runs to the next <h2>/<h3>.
    heads = list(re.finditer(
        r'<h3\b[^>]*?\bid="(\d{4}-\d{2}-\d{2})-[^"]+"'
        r'|<h3[^>]*>\s*<div class="group relative pt-6 pb-2" id="(\d{4}-\d{2}-\d{2})-[^"]+"',
        html,
    ))
    for head in heads:
        ann_date = head.group(1) or head.group(2)
        nxt = re.compile(r"<h[23]\b").search(html, head.end())
        body = html[head.end():nxt.start() if nxt else len(html)]
        if len(body) > 40000:
            continue
        for tbl in re.findall(r"<table[^>]*>(.*?)</table>", body, flags=re.S):
            rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, flags=re.S)
            if not rows:
                continue
            cells0 = [_strip_tags(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", rows[0], flags=re.S)]
            if not cells0 or "Retirement date" not in cells0[0]:
                continue
            for row in rows[1:]:
                cells = [_strip_tags(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, flags=re.S)]
                if len(cells) < 3:
                    continue
                ret_iso = _parse_us_date(cells[0])
                if not ret_iso:
                    continue
                for old in re.split(r"[,\s]+", cells[1]):
                    if not old:
                        continue
                    records.append({
                        "vendor": "Anthropic",
                        "model_id": old,
                        "family": _family_from_claude_id(old),
                        "kind": "lifecycle",
                        "status": "retired" if ret_iso <= _today_iso() else "deprecated",
                        "announced": ann_date,
                        "retirement": ret_iso,
                        "replacement": cells[2] or None,
                        "source_url": ANTHROPIC_DEPRECATIONS_URL,
                        "evidence": f"Announced {ann_date}; retirement {ret_iso}"
                                    + (f"; replacement {cells[2]}" if cells[2] else ""),
                    })
    return records


def parse_anthropic_deprecations(html: str, fetched_at: str = "") -> List[dict]:
    """Anthropic model-deprecations page: lifecycle table + history sections."""
    records: List[dict] = []
    # --- model status table, columns mapped by label ------------------------
    status_rows = None
    for tbl in re.findall(r"<table[^>]*>(.*?)</table>", html, flags=re.S):
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, flags=re.S)
        header = [_strip_tags(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", rows[0], flags=re.S)] if rows else []
        if header and header[0] == "API model name":
            want = ["API model name", "Current state", "Deprecated", "Tentative retirement date"]
            missing = [w for w in want if w not in header]
            if missing:
                raise ValueError(f"Anthropic status table: missing column(s) {missing}; header {header}")
            cols = [header.index(w) for w in want]
            status_rows = [[cells[i] if i < len(cells) else "" for i in cols]
                           for cells in ([_strip_tags(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", r, flags=re.S)] for r in rows[1:])
                           if cells]
            break
    if status_rows is None:
        raise ValueError("Anthropic deprecations page: no model status table ('API model name' header) found")
    for model_id, status, announced, retirement in status_rows:
        if not model_id:
            continue
        if status not in ("Active", "Deprecated", "Retired"):
            raise ValueError(f"Anthropic status table: unknown state {status!r} for {model_id}")
        announced_iso = _parse_us_date(announced) if announced and announced != "N/A" else None
        ret_iso = _parse_us_date(retirement) if retirement and retirement not in ("N/A", "To be announced") else None
        if retirement and retirement not in ("N/A", "To be announced") and not ret_iso and not _parse_not_sooner(retirement):
            raise ValueError(f"Anthropic status table: unreadable retirement {retirement!r} for {model_id}")
        # "Not sooner than <date>" on an active row is an earliest-retirement
        # BOUND, not a retirement date — keep it in its own field so it is
        # never quoted as an actual retirement.
        not_before = _parse_not_sooner(retirement) if status == "Active" else None
        rec = {
            "vendor": "Anthropic",
            "model_id": model_id,
            "family": _family_from_claude_id(model_id),
            "kind": "lifecycle",
            "status": status.lower(),
            "stated_status": status,
            "announced": announced_iso,
            "retirement": ret_iso,
            "retirement_not_before": not_before,
            "replacement": None,
            "source_url": ANTHROPIC_DEPRECATIONS_URL,
            "evidence": (f"{status}; retirement {retirement}" if ret_iso or not_before
                         else f"{status}; deprecated {announced}; tentative retirement date: {retirement}" if status == "Deprecated"
                         else f"{status}; no retirement announced"),
            "note": None,
        }
        if rec["status"] == "active" and rec["retirement"] is None:
            rec["note"] = "No retirement announced" if not not_before else None
        elif rec["status"] == "deprecated" and rec["retirement"] is None:
            rec["note"] = "Deprecated with no retirement date published"
        records.append(rec)

    # --- merge history sections (announced dates + replacements) ------------
    merged: dict = {}
    for rec in records + _parse_anthropic_history(html):
        key = (rec["model_id"], rec["retirement"])
        if key not in merged:
            merged[key] = rec
            continue
        have, other = merged[key], rec
        for field in ("announced", "replacement", "note", "status", "evidence"):
            if not have.get(field) and other.get(field):
                have[field] = other[field]
    records = list(merged.values())

    # dedupe on model_id, preferring the record with a concrete retirement
    # date over the bound-only record
    by_id: dict = {}
    for rec in records:
        key = rec["model_id"]
        if key not in by_id or (rec.get("retirement") and not by_id[key].get("retirement")):
            by_id[key] = rec
    records = list(by_id.values())

    # --- deprecated-but-not-retired (prose) ---------------------------------
    text = _text(html)
    m = re.search(r"Claude Mythos Preview\s*\(\s*(claude-mythos-preview)\s*\) is deprecated", text)
    if m and m.group(1) not in by_id:
        records.append({
            "vendor": "Anthropic",
            "model_id": m.group(1),
            "family": "Claude Mythos Preview",
            "kind": "lifecycle",
            "status": "deprecated",
            "announced": None,
            "retirement": None,
            "replacement": "claude-mythos-5",
            "source_url": ANTHROPIC_DEPRECATIONS_URL,
            "evidence": "Claude Mythos Preview (claude-mythos-preview) is deprecated",
            "note": "Deprecated with no retirement date published",
        })
    for rec in records:
        rec["fetched_at"] = fetched_at or _fetched_at()
    return records


# ---------------------------------------------------------------------------
# Anthropic — models overview (current models)
# ---------------------------------------------------------------------------

def parse_anthropic_models(html: str, fetched_at: str = "") -> List[dict]:
    """The overview page's single table has a 'Claude API ID' row whose cells
    are the canonical current model ids. Family names come from the header
    row (Claude Fable 5, Claude Opus 5, ...)."""
    records: List[dict] = []
    tbls = re.findall(r"<table[^>]*>(.*?)</table>", html, flags=re.S)
    for tbl in tbls:
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, flags=re.S)
        ids_row = None
        header_row = None
        for row in rows:
            # Oct 2026: header cells carry a tagline in a text-caption span.
            row = re.sub(r'<span class="text-caption[^"]*">.*?</span>', "", row, flags=re.S)
            cells = [_strip_tags(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, flags=re.S)]
            if not cells:
                continue
            if cells[0] == "Claude API ID":
                ids_row = cells[1:]
            elif cells[0] == "Feature" and not header_row:
                header_row = cells[1:]
        if ids_row is None:
            continue
        for i, mid in enumerate(ids_row):
            mid = mid.split(" ")[0]  # AWS-style cells carry footnote digits
            if not re.match(r"^claude-[a-z0-9.-]+$", mid):
                continue
            family = header_row[i] if header_row and i < len(header_row) else _family_from_claude_id(mid)
            records.append({
                "vendor": "Anthropic",
                "model_id": mid,
                "family": family,
                "kind": "current",
                "status": "listed",
                "announced": None,
                "retirement": None,
                "replacement": None,
                "source_url": ANTHROPIC_MODELS_URL,
                "evidence": f"Listed on the model overview table as {family} ({mid})",
            })
    if not records:
        raise ValueError("Anthropic models overview: no 'Claude API ID' row with model ids found")
    for rec in records:
        rec["fetched_at"] = fetched_at or _fetched_at()
    return records


# ---------------------------------------------------------------------------
# OpenAI — deprecations page (lifecycle records)
# ---------------------------------------------------------------------------

def parse_openai_deprecations(html: str, fetched_at: str = "") -> List[dict]:
    """Deprecation sections: 'YYYY-MM-DD: <title>' header + table with
    Shutdown date | Model/system | Recommended replacement columns."""
    records: List[dict] = []
    for tbl in re.findall(r"<table[^>]*>(.*?)</table>", html, flags=re.S):
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, flags=re.S)
        if not rows:
            continue
        header = [_strip_tags(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", rows[0], flags=re.S)]
        if not any(("replacement" in h.lower() or "substitute" in h.lower()) for h in header):
            continue
        try:
            i_date = next(i for i, h in enumerate(header) if "shut" in h.lower() or "retir" in h.lower())
            i_model = next(i for i, h in enumerate(header) if "model" in h.lower() or "system" in h.lower())
            i_repl = next(i for i, h in enumerate(header) if "replacement" in h.lower() or "substitute" in h.lower())
        except StopIteration:
            continue
        tbl_pos = html.find(tbl)
        before = html[max(0, tbl_pos - 30000):tbl_pos]
        m = re.findall(r"(\d{4}-\d{2}-\d{2}):", before)
        ann_date = m[-1] if m else None

        for row in rows[1:]:
            cells = [_strip_tags(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, flags=re.S)]
            if len(cells) <= max(i_date, i_model, i_repl):
                continue
            ret_date = _parse_us_date(cells[i_date])
            if not ret_date:
                continue
            for mid in re.split(r"[|,]+", cells[i_model]):
                mid = mid.strip()
                if not mid:
                    continue
                # API/system deprecations are not models; model IDs never
                # contain spaces/equals/colons (S126 trap).
                if mid.startswith("/") or "api" in mid.lower() or mid.startswith("OpenAI-Beta"):
                    continue
                if " " in mid or "=" in mid or ":" in mid:
                    continue
                status = "retired" if ret_date <= _today_iso() else "deprecated"
                records.append({
                    "vendor": "OpenAI",
                    "model_id": mid,
                    "family": mid,
                    "kind": "lifecycle",
                    "status": status,
                    "announced": ann_date,
                    "retirement": ret_date,
                    "replacement": cells[i_repl] or None,
                    "source_url": OPENAI_DEPRECATIONS_URL,
                    "evidence": f"Announced {ann_date}; shutdown {ret_date}"
                                + (f"; replacement {cells[i_repl]}" if cells[i_repl] else ""),
                })
    if not records:
        raise ValueError("OpenAI deprecations page: no shutdown/replacement tables parsed")
    seen = set()
    out = []
    for rec in records:
        key = (rec["model_id"], rec["retirement"])
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
    for rec in out:
        rec["fetched_at"] = fetched_at or _fetched_at()
    return out


# ---------------------------------------------------------------------------
# OpenAI — models page (current models)
# ---------------------------------------------------------------------------

def parse_openai_models(html: str, fetched_at: str = "") -> List[dict]:
    """Current models page: each model card carries 'Model ID <id>' prose.
    Also captures aliases ('Alias <id>')."""
    text = _text(html)
    records: List[dict] = []
    seen = set()
    for m in re.finditer(r"Model ID\s+(gpt-[a-z0-9.-]+)", text):
        mid = m.group(1)
        if mid in seen:
            continue
        seen.add(mid)
        # evidence: the sentence containing the Model ID mention (start at the
        # previous sentence boundary so the quote reads cleanly)
        start = max(0, m.start() - 220)
        prev = text.rfind(". ", start, m.start())
        if prev > 0:
            start = prev + 2
        snippet = text[start:m.end() + 80].strip()
        # Card title just before the tagline: "... GPT-6.1 Sol Near-Astra ... Model ID gpt-6.1-sol".
        family = mid
        head = text[max(0, m.start() - 220):m.start()]
        titles = [t for t in re.findall(r"(GPT-[0-9][0-9.]*(?: [A-Z][a-z]+)?)", head) if normalize_display(t) == mid]
        if titles:
            family = titles[-1]
        records.append({
            "vendor": "OpenAI",
            "model_id": mid,
            "family": family,
            "kind": "current",
            "status": "listed",
            "announced": None,
            "retirement": None,
            "replacement": None,
            "source_url": OPENAI_MODELS_URL,
            "evidence": ("Listed as a current model" + (f" ({family})" if family != mid else "") + ": " + snippet[-200:]),
        })
    if not records:
        raise ValueError("OpenAI models page: no 'Model ID gpt-…' cards found")
    for rec in records:
        rec["fetched_at"] = fetched_at or _fetched_at()
    return records


# ---------------------------------------------------------------------------
# DeepSeek — api-docs (current models; no lifecycle page exists)
# ---------------------------------------------------------------------------

def parse_deepseek_docs(html: str, fetched_at: str = "") -> List[dict]:
    """DeepSeek publishes no deprecation/lifecycle page. The api-docs landing
    page's PARAM/VALUE table has a `model` row whose <code> cells are the
    current API model names, and a footnote under it. Two footnote wordings
    are known; any other wording fails the build (fail closed):

      Aug 2026: "The deepseek-v4-flash model has been updated to
                 DeepSeek-V4-Flash-0731, and the deepseek-v4-pro model has
                 been updated to DeepSeek-V4-Pro-0813."
      Oct 2026: "Use deepseek-flash as the model name. The legacy names
                 deepseek-v4-flash and deepseek-v4-flash-vision-exp are still
                 accepted, but the corresponding models have been retired,
                 their requests are served by the DeepSeek-V4.1-Flash model..."
    """
    ids: List[str] = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)
        # Label may carry a footnote marker: "model(1)" in the Aug 2026 layout.
        if len(cells) >= 2 and re.sub(r"\(\d+\)", "", _strip_tags(cells[0])).strip().lower() == "model":
            ids = [_strip_tags(c).lower() for c in re.findall(r"<code>(.*?)</code>", cells[1], flags=re.S)]
            break
    if not ids or not all(re.match(r"^deepseek-[a-z0-9.-]+$", i) for i in ids):
        raise ValueError(f"DeepSeek docs: model row not found or unreadable ({ids})")
    text = _text(html)
    records: List[dict] = []

    def rec(mid, family, kind, status, evidence, **extra):
        records.append({
            "vendor": "DeepSeek", "model_id": mid, "family": family, "kind": kind,
            "status": status, "announced": None, "retirement": None, "replacement": None,
            "source_url": DEEPSEEK_DOCS_URL, "evidence": evidence, **extra,
        })

    upd = re.search(
        r"The (deepseek-[a-z0-9.-]+) model has been updated to ([^.,]+), and the "
        r"(deepseek-[a-z0-9.-]+) model has been updated to ([^.,]+?)\.(?:\s|$)",
        text,
    )
    legacy = re.search(
        r"Use (deepseek-[a-z0-9.-]+) as the model name\. The legacy names ([^.]+?) are still accepted, "
        r"but the corresponding models have been retired, their requests are served by the "
        r"([A-Za-z0-9.-]+) model and billed at the ([A-Za-z]+) price\.",
        text,
    )
    if "(1)" in text and not (upd or legacy):
        raise ValueError("DeepSeek docs: model footnote wording not recognized; inspect before parsing")

    updated_to = {}
    if upd:
        updated_to = {upd.group(1).lower(): upd.group(2).strip(), upd.group(3).lower(): upd.group(4).strip()}
    for mid in ids:
        rec(mid, mid, "current", "listed", "Listed in the API docs model table"
            + (f"; updated to {updated_to[mid]}" if mid in updated_to else ""))
    if upd:
        for base, ver in ((upd.group(1), upd.group(2).strip()), (upd.group(3), upd.group(4).strip())):
            if ver.lower() not in ids:
                rec(ver.lower(), ver, "current", "listed", f"Named on the API docs page as the current version of {base}")
    if legacy:
        served_by = legacy.group(3)
        sentence = legacy.group(0)  # verbatim vendor text
        for old in re.findall(r"deepseek-[a-z0-9.-]+", legacy.group(2)):
            # Evidence is the vendor's sentence verbatim (the page shows it in quotes).
            rec(old, old, "lifecycle", "retired", sentence,
                replacement=legacy.group(1), stated_status="retired")
        if served_by.lower() not in ids:
            rec(served_by.lower(), served_by, "current", "listed",
                "Named on the API docs page as the model that serves requests sent to the retired legacy names")
    for r in records:
        r["fetched_at"] = fetched_at or _fetched_at()
    return records


# ---------------------------------------------------------------------------
# Kimi — platform page (current models via quickstart doc links; no lifecycle)
# ---------------------------------------------------------------------------

def parse_kimi_platform(html: str, fetched_at: str = "") -> List[dict]:
    """The platform page links quickstart docs per model:
    /docs/guide/kimi-k2-6-quickstart, /docs/guide/kimi-k3-quickstart, ...
    No deprecation page exists — retirement claims are unverifiable (gap)."""
    records: List[dict] = []
    seen = set()
    for m in re.finditer(r'href="(/docs/guide/(kimi-[a-z0-9-]+)-quickstart)"', html):
        mid = m.group(2)
        if mid in seen:
            continue
        seen.add(mid)
        records.append({
            "vendor": "Kimi",
            "model_id": mid,
            "family": mid,
            "kind": "current",
            "status": "listed",
            "announced": None,
            "retirement": None,
            "replacement": None,
            "source_url": KIMI_PLATFORM_URL,
            "evidence": f"Quickstart doc linked from the platform page: {m.group(1)}",
        })
    if not records:
        raise ValueError("Kimi platform page: no /docs/guide/kimi-…-quickstart links found")
    for rec in records:
        rec["fetched_at"] = fetched_at or _fetched_at()
    return records


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

PARSERS = {
    "anthropic_deprecations": parse_anthropic_deprecations,
    "anthropic_models": parse_anthropic_models,
    "openai_deprecations": parse_openai_deprecations,
    "openai_models": parse_openai_models,
    "deepseek_docs": parse_deepseek_docs,
    "kimi_platform": parse_kimi_platform,
}


def main() -> None:
    import json
    import sys
    from pathlib import Path

    src = sys.argv[1] if len(sys.argv) > 1 else None
    if not src or src not in PARSERS:
        print("usage: parser.py <source-name> <html-file>", file=sys.stderr)
        sys.exit(1)
    html = Path(sys.argv[2]).read_text(encoding="utf-8")
    records = PARSERS[src](html)
    print(json.dumps(records, indent=2))
    print(f"\n{len(records)} records", file=sys.stderr)


if __name__ == "__main__":
    main()
