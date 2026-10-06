#!/usr/bin/env python3
"""
match.py — Claim parsing + verdict logic for the Model Release Status Verifier.

This is the REFERENCE implementation. The frontend ships an exact JS mirror
(frontend/matcher.js; app.js only calls it) and tests/test_match_js.js runs the
SAME case table through both — zero diffs allowed (coupled-pair discipline, S123).

A claim is a free-text sentence ("DeepSeek released V4-Flash on July 31") or
structured fields (model + status + date). Verdicts:

  confirmed     — the vendor's own page supports the claim
  contradicted  — the vendor's own page shows a different state (or no such model)
  partial       — some part confirmed, some part unverifiable (e.g. existence
                  confirmed but the claimed release DATE has no vendor surface)
  unverifiable  — no vendor surface exists for this claim class (documented
                  gap — never "clean by absence")

Model identification is three-layer:
  1. Direct id pattern (claude-*, gpt-*/o*, deepseek-*, kimi-*/moonshot-*).
  2. Short aliases ("V4 Flash" -> deepseek-v4-flash) gated on the vendor
     keyword appearing in the claim. Shipped in index["short_aliases"] so the
     JS mirror reads the same table.
  3. Family-name lookup ("Claude Fable 5") against record family fields,
     longest family first, with a boundary guard ("Opus 4.1" must not match
     "Opus 4.10").

Record matching uses base-id equality: "claude-opus-4-1" matches
"claude-opus-4-1-20250805" (strip a trailing -YYYYMMDD from both sides), so
dated snapshot ids and their base names are the same model.

Coverage rule (honest-coverage, S119): DeepSeek and Kimi publish NO lifecycle
page. Availability/existence claims against them are verifiable from their
current-model pages; retirement/deprecation claims are unverifiable with the
gap named.
"""

from __future__ import annotations

import re
from typing import Optional

# --- model-name -> vendor patterns --------------------------------------
# Order matters: more specific first.
MODEL_PATTERNS = [
    ("Anthropic", re.compile(r"claude-[a-z0-9][a-z0-9.-]*", re.I)),
    ("DeepSeek", re.compile(r"deepseek-[a-z0-9][a-z0-9.-]*", re.I)),
    ("Kimi", re.compile(r"(?:kimi|moonshot)-[a-z0-9][a-z0-9.-]*", re.I)),
    ("OpenAI", re.compile(r"chatgpt-[a-z0-9][a-z0-9.-]*", re.I)),
    ("OpenAI", re.compile(r"(?:gpt-oss|gpt)-[a-z0-9][a-z0-9.-]*", re.I)),
    ("OpenAI", re.compile(r"\bo[0-9]+(?:-[a-z0-9]+)?\b", re.I)),
]

VENDOR_KEYWORDS = {
    "Anthropic": ["anthropic", "claude"],
    "OpenAI": ["openai", "chatgpt", "gpt"],
    "DeepSeek": ["deepseek"],
    "Kimi": ["kimi", "moonshot"],
}

RETIREMENT_KEYWORDS = [
    "retired", "retirement", "retiring", "will retire", "to retire",
    "deprecated", "deprecation", "shut down", "shutdown", "sunset",
    "discontinued", "decommissioned", "end of life", "eol",
]
AVAILABILITY_KEYWORDS = [
    "released", "release", "launched", "launch", "available", "availability",
    "generally available", " ga", "ga ", "preview", "announced", "announce",
    "shipped", "introduced", "unveiled", "out now", "is live", "goes live",
]

# date forms accepted in a claim
DATE_PATTERNS = [
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),                                  # ISO
    re.compile(r"\b([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b"),   # July 31, 2026
    re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9}),?\s+(\d{4})\b"),                   # 31 July 2026
    re.compile(r"\b([A-Za-z]{3,9})\s+(\d{4})\b"),                                 # July 2026
]

MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12,
}
MONTHS_ABBR = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def normalize_model_id(mid: str) -> str:
    """Lowercase, trim, collapse internal whitespace to single hyphens.
    'DeepSeek-V4 Flash' -> 'deepseek-v4-flash'. Version suffixes are kept."""
    mid = mid.strip().lower()
    mid = re.sub(r"\s+", "-", mid)
    mid = re.sub(r"-+", "-", mid)
    return mid


def base_id(mid: str) -> str:
    """Strip a trailing snapshot suffix: Anthropic uses compact -YYYYMMDD
    (claude-opus-4-1-20250805), OpenAI uses -YYYY-MM-DD (gpt-4o-2024-05-13).
    'gpt-4o' -> 'gpt-4o'. Non-date suffixes ('gpt-4-0613') are kept."""
    mid = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", mid)
    mid = re.sub(r"-\d{8}$", "", mid)
    return mid


def normalize_family(name: str) -> str:
    """'Claude Opus 4.1' -> 'claude opus 4.1' (collapse whitespace only)."""
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _vendor_keyword_in(claim_low: str, vendor: str) -> bool:
    return any(k in claim_low for k in VENDOR_KEYWORDS.get(vendor, []))


def _extend_id(claim: str, pos: int, mid: str, vendor: str, index: dict) -> Optional[tuple]:
    """(extended id, span end) when mid plus the next one or two words names a
    model in the index, longest first; None otherwise."""
    known = set()
    for rec in index.get("models", []):
        if rec.get("vendor") == vendor:
            rid = normalize_model_id(rec["model_id"])
            known.add(rid)
            known.add(base_id(rid))
    parts, ends = [], []
    for _ in range(2):
        w = re.match(r"[ \t]+([A-Za-z0-9][A-Za-z0-9.]*)", claim[pos:])
        if not w:
            break
        word = w.group(1).rstrip(".")
        parts.append(word.lower())
        ends.append(pos + w.end() - (len(w.group(1)) - len(word)))
        pos += w.end()
    for k in range(len(parts), 0, -1):
        cand = mid + "-" + "-".join(parts[:k])
        if cand in known:
            return cand, ends[k - 1]
    return None


def identify(claim: str, index: dict) -> tuple[Optional[str], Optional[str], str, Optional[tuple]]:
    """Identify (model_ref, vendor, method, span) from the claim.
    model_ref is either a normalized model id or a normalized family name.
    method is 'id' | 'alias' | 'family'. span is (start, end) in the claim
    for the matched text, so the caller can mask it before date extraction
    (a snapshot date inside a model id is NOT the claim's date)."""
    # layer 1: direct id pattern, extended by up to two following words when
    # the extended id is a known model ("GPT-6.1 Sol" -> gpt-6.1-sol)
    for vendor, pat in MODEL_PATTERNS:
        m = pat.search(claim)
        if m:
            mid = normalize_model_id(m.group(0))
            ext = _extend_id(claim, m.end(), mid, vendor, index)
            if ext:
                return ext[0], vendor, "id", (m.start(), ext[1])
            return mid, vendor, "id", m.span()

    claim_low = claim.lower()

    # layer 2: short aliases, gated on the vendor keyword
    for alias_key, (vendor, canonical) in index.get("short_aliases", {}).items():
        pos = claim_low.find(alias_key)
        if pos >= 0 and _vendor_keyword_in(claim_low, vendor):
            return canonical, vendor, "alias", (pos, pos + len(alias_key))

    # layer 3: family-name lookup, longest first, boundary-guarded
    families = {}
    for rec in index.get("models", []):
        fam = normalize_family(rec.get("family") or "")
        if fam:
            families[fam] = rec["vendor"]
    for fam in sorted(families, key=len, reverse=True):
        pos = claim_low.find(fam)
        while pos >= 0:
            after = claim_low[pos + len(fam):pos + len(fam) + 1]
            if not after or not re.match(r"[a-z0-9.]", after):
                return fam, families[fam], "family", (pos, pos + len(fam))
            pos = claim_low.find(fam, pos + 1)

    return None, None, "none", None


def extract_date(claim: str) -> Optional[str]:
    """First date in the claim, normalized to ISO (day-precision where present)."""
    m = DATE_PATTERNS[0].search(claim)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = DATE_PATTERNS[1].search(claim)
    if m:
        mon = MONTHS.get(m.group(1).title()) or MONTHS_ABBR.get(m.group(1).title()[:3])
        if mon is not None:
            return f"{m.group(3)}-{mon:02d}-{int(m.group(2)):02d}"
    m = DATE_PATTERNS[2].search(claim)
    if m:
        mon = MONTHS.get(m.group(2).title()) or MONTHS_ABBR.get(m.group(2).title()[:3])
        if mon is not None:
            return f"{m.group(3)}-{mon:02d}-{int(m.group(1)):02d}"
    m = DATE_PATTERNS[3].search(claim)
    if m:
        mon = MONTHS.get(m.group(1).title()) or MONTHS_ABBR.get(m.group(1).title()[:3])
        if mon is not None:
            return f"{m.group(2)}-{mon:02d}"
    return None


def date_mentioned_without_year(claim: str) -> bool:
    """True when the claim names a month+day (or month) but no year — the date
    is present but uncheckable. Never silently dropped."""
    low = claim.lower()
    for mon in list(MONTHS) + list(MONTHS_ABBR):
        if re.search(r"\b" + mon + r"\b", low, re.I):
            if re.search(r"\b\d{4}\b", low) is None:
                return True
    return False


def claim_class(claim: str) -> str:
    """'retirement' | 'availability' | 'unknown'. Retirement keywords win on
    ties (a sentence saying 'retired after being released in 2024' is a
    retirement claim)."""
    low = claim.lower()
    if any(k in low for k in RETIREMENT_KEYWORDS):
        return "retirement"
    if any(k in low for k in AVAILABILITY_KEYWORDS):
        return "availability"
    return "unknown"


def _same_month_iso(a: str, b: str) -> bool:
    return a[:7] == b[:7]


def _same_day_iso(a: str, b: str) -> bool:
    if len(a) == 10 and len(b) == 10:
        return a == b
    return a[:7] == b[:7]


def _collect_matches(vendor: str, model_ref: str, index: dict) -> list:
    """Index records matching the claim's model reference, by id equality,
    base-id equality, or family equality."""
    ref = normalize_model_id(model_ref)
    fam = normalize_family(model_ref)
    out = []
    for rec in index.get("models", []):
        if rec.get("vendor") != vendor:
            continue
        rid = normalize_model_id(rec["model_id"])
        if rid == ref or base_id(rid) == base_id(ref):
            out.append(rec)
            continue
        if normalize_family(rec.get("family") or "") == fam:
            out.append(rec)
    return out


def verify_claim(claim: str, index: dict) -> dict:
    """Verify a free-text claim against the built index."""
    model_ref, vendor, method, span = identify(claim, index)
    # Mask the model span before date extraction: a snapshot date embedded in
    # a model id ("gpt-4o-2024-05-13") is not the claim's date.
    date_claim = claim
    if span is not None:
        date_claim = claim[:span[0]] + " " + claim[span[1]:]
    date_iso = extract_date(date_claim)
    date_unparsed = date_mentioned_without_year(date_claim) and date_iso is None
    cls = claim_class(claim)

    if model_ref is None:
        return {
            "verdict": "unverifiable",
            "reason": "No recognizable model name in the claim. Supported families: claude-* (Anthropic), gpt-*/o* (OpenAI), deepseek-*, kimi-*/moonshot-* (Kimi).",
            "model": None, "vendor": None, "claimed_date": date_iso,
            "date_unparsed": date_unparsed,
            "claim_class": cls, "matches": [],
        }

    coverage = index.get("coverage", {}).get(vendor, {})
    has_lifecycle = bool(coverage.get("has_lifecycle_page"))
    matches = _collect_matches(vendor, model_ref, index)

    base = {
        "model": model_ref,
        "vendor": vendor,
        "claimed_date": date_iso,
        "date_unparsed": date_unparsed,
        "claim_class": cls,
        "matches": matches,
    }

    # --- no record for this model anywhere in the index --------------------
    if not matches:
        if cls == "retirement" and not has_lifecycle:
            return {**base, "verdict": "unverifiable", "reason": (
                f"{vendor} publishes no model-deprecation page, so retirement "
                f"claims cannot be checked against a vendor surface. This is a "
                f"documented gap, not a clean bill of health."
            )}
        if cls == "retirement":
            return {**base, "verdict": "contradicted", "reason": (
                f"{vendor}'s deprecation page lists no retirement or deprecation "
                f"for {model_ref}. If a retirement was announced, the vendor "
                f"has not published it."
            )}
        if cls == "availability" and has_lifecycle:
            return {**base, "verdict": "contradicted", "reason": (
                f"{model_ref} does not appear on {vendor}'s current-model page "
                f"or its deprecation page. No vendor surface shows this model "
                f"as available."
            )}
        if cls == "availability" and not has_lifecycle:
            return {**base, "verdict": "contradicted", "reason": (
                f"{model_ref} does not appear on {vendor}'s current-model "
                f"documentation. (Note: {vendor} publishes no lifecycle page, "
                f"so retirement history cannot be checked.)"
            )}
        return {**base, "verdict": "unverifiable", "reason": (
            f"No vendor record found for {model_ref}, and the claim class "
            f"could not be determined."
        )}

    # --- availability / existence claims -----------------------------------
    if cls == "availability":
        current = [r for r in matches if r.get("kind") == "current"]
        retired = [r for r in matches if r.get("status") == "retired"]
        deprecated = [r for r in matches if r.get("status") == "deprecated"]
        if current:
            if date_iso:
                return {**base, "verdict": "partial", "reason": (
                    f"{model_ref} is listed as a current {vendor} model — "
                    f"existence confirmed. The claimed date ({date_iso}) could "
                    f"not be checked: {vendor} does not publish release dates "
                    f"on its model pages."
                )}
            if date_unparsed:
                return {**base, "verdict": "partial", "reason": (
                    f"{model_ref} is listed as a current {vendor} model — "
                    f"existence confirmed. The claim mentions a date but no "
                    f"year, and {vendor} does not publish release dates "
                    f"anyway — the date could not be checked."
                )}
            return {**base, "verdict": "confirmed", "reason": (
                f"{model_ref} is listed on {vendor}'s current-model page."
            )}
        if retired:
            ret = retired[0].get("retirement")
            return {**base, "verdict": "partial", "reason": (
                f"{vendor} records show {model_ref} was released, but it is "
                f"now listed as retired"
                + (f" (retirement {ret})" if ret else "")
                + " — if the claim means 'currently available', it is wrong."
            )}
        if deprecated:
            return {**base, "verdict": "partial", "reason": (
                f"{model_ref} exists but {vendor} lists it as deprecated"
                + (f", retirement {deprecated[0]['retirement']}" if deprecated[0].get("retirement") else "")
                + " — not a current model."
            )}
        active = [r for r in matches if r.get("status") == "active"]
        if active:
            ret = active[0].get("retirement")
            return {**base, "verdict": "confirmed", "reason": (
                f"{vendor} lists {model_ref} as active"
                + (f" with retirement {ret}" if ret else "")
                + "."
            )}
        return {**base, "verdict": "unverifiable", "reason": (
            f"A vendor record exists for {model_ref} but its status could not "
            f"be classified."
        )}

    # --- retirement / deprecation claims -----------------------------------
    if cls == "retirement":
        # A vendor without a lifecycle page can still state a retirement
        # elsewhere (DeepSeek's model-table footnote); use the stated record.
        stated = [r for r in matches if r.get("kind") == "lifecycle"]
        if not has_lifecycle and not stated:
            return {**base, "verdict": "unverifiable", "reason": (
                f"{vendor} publishes no model-deprecation page, so retirement "
                f"claims cannot be checked. (The model may exist — see its "
                f"current-model listing — but no retirement surface exists.)"
            )}
        retired = [r for r in matches if r.get("status") in ("retired", "deprecated") and r.get("retirement")]
        if not retired:
            # retired with no retirement date published (e.g. deepseek-v4-flash)
            retired_nodate = [r for r in matches if r.get("status") == "retired" and not r.get("retirement")]
            if retired_nodate:
                if date_iso:
                    return {**base, "verdict": "partial", "reason": (
                        f"{vendor} states {model_ref} has been retired but "
                        f"publishes no retirement date, so the claimed date "
                        f"({date_iso}) cannot be checked."
                    )}
                return {**base, "verdict": "confirmed", "reason": (
                    f"{vendor} states {model_ref} has been retired. No "
                    f"retirement date is published."
                )}
            # deprecated with no retirement date published (e.g. claude-mythos-preview)
            depr_nodate = [r for r in matches if r.get("status") == "deprecated" and not r.get("retirement")]
            if depr_nodate:
                if "deprecat" in claim.lower():
                    return {**base, "verdict": "confirmed", "reason": (
                        f"{vendor} lists {model_ref} as deprecated"
                        + (f" — {depr_nodate[0].get('evidence')}" if depr_nodate[0].get("evidence") else "")
                    )}
                return {**base, "verdict": "contradicted", "reason": (
                    f"{vendor} lists {model_ref} as deprecated, with no "
                    f"retirement date published — it is not recorded as retired."
                )}
            current = [r for r in matches if r.get("kind") == "current"]
            bound_recs = [r for r in matches if r.get("retirement_not_before")]
            if current and not bound_recs:
                return {**base, "verdict": "contradicted", "reason": (
                    f"{vendor}'s deprecation page lists no retirement for "
                    f"{model_ref}, and it is still listed as a current model."
                )}
            if bound_recs:
                bound = bound_recs[0]["retirement_not_before"]
                if date_iso and date_iso < bound:
                    return {**base, "verdict": "contradicted", "reason": (
                        f"{vendor} lists {model_ref} as active with retirement "
                        f"not sooner than {bound}. A retirement date of "
                        f"{date_iso} contradicts the vendor's own bound."
                    )}
                return {**base, "verdict": "partial", "reason": (
                    f"{vendor} lists {model_ref} as active with retirement "
                    f"not sooner than {bound} — no actual retirement date is "
                    f"published, so the claim cannot be confirmed."
                )}
            return {**base, "verdict": "contradicted", "reason": (
                f"{vendor}'s deprecation page lists no retirement for {model_ref}."
            )}
        r = retired[0]
        if date_iso:
            if _same_day_iso(date_iso, r["retirement"]):
                return {**base, "verdict": "confirmed", "reason": (
                    f"{vendor} records {model_ref} retirement as "
                    f"{r['retirement']} — matches the claimed date."
                )}
            if _same_month_iso(date_iso, r["retirement"]):
                return {**base, "verdict": "partial", "reason": (
                    f"{vendor} records {model_ref} retirement as "
                    f"{r['retirement']}; the claim says {date_iso}. Same "
                    f"month, different day — likely rounding in the claim."
                )}
            return {**base, "verdict": "contradicted", "reason": (
                f"{vendor} records {model_ref} retirement as {r['retirement']}, "
                f"but the claim says {date_iso}."
            )}
        return {**base, "verdict": "confirmed", "reason": (
            f"{vendor} records {model_ref} as "
            f"{r['status']} (retirement {r['retirement']})."
        )}

    # --- unknown class: fall back to what the record says -------------------
    current = [r for r in matches if r.get("kind") == "current"]
    retired = [r for r in matches if r.get("status") == "retired"]
    if current:
        return {**base, "verdict": "partial", "reason": (
            f"{model_ref} is listed as a current {vendor} model, but the "
            f"claim's class (released? retired?) could not be determined — "
            f"restate it with a status word."
        )}
    if retired:
        return {**base, "verdict": "partial", "reason": (
            f"{vendor} lists {model_ref} as retired ({retired[0].get('retirement')}), "
            f"but the claim's class could not be determined."
        )}
    return {**base, "verdict": "unverifiable", "reason": (
        f"A vendor record exists for {model_ref} but neither the record's "
        f"status nor the claim's class could be determined."
    )}


def main() -> None:
    import json
    import sys

    if len(sys.argv) < 3:
        print("usage: match.py <index.json> '<claim text>'", file=sys.stderr)
        sys.exit(1)
    index = json.load(open(sys.argv[1], encoding="utf-8"))
    print(json.dumps(verify_claim(sys.argv[2], index), indent=2, default=str))


if __name__ == "__main__":
    main()
