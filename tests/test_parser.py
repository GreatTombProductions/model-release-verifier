#!/usr/bin/env python3
"""Parser unit tests — pure functions, offline HTML fixtures."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import parser as P  # noqa: E402


def check(name, cond):
    if cond:
        print(f"OK   {name}")
    else:
        print(f"FAIL {name}")
        sys.exit(1)


# --- Anthropic deprecations fixture -----------------------------------------
ANTHROPIC_DEPRECATIONS = """
<html><body>
<table><thead><tr><th>API model name</th><th>Current state</th><th>Deprecated</th><th>Tentative retirement date</th></tr></thead><tbody>
<tr><td>claude-opus-4-1-20250805</td><td>Retired</td><td>June 5, 2026</td><td>August 5, 2026</td></tr>
<tr><td>claude-fable-5</td><td>Active</td><td>N/A</td><td>Not sooner than June 9, 2027</td></tr>
</tbody></table>
<h3><div class="group relative pt-6 pb-2" id="2026-06-05-claude-opus-4-1-model"></div></h3>
<table><tbody>
<tr><td>Retirement date</td><td>Deprecated model</td><td>Recommended replacement</td></tr>
<tr><td>August 5, 2026</td><td>claude-opus-4-1-20250805</td><td>claude-opus-4-8</td></tr>
</tbody></table>
<p>Claude Mythos Preview (claude-mythos-preview) is deprecated</p>
</body></html>
"""


def test_anthropic_deprecations():
    recs = P.parse_anthropic_deprecations(ANTHROPIC_DEPRECATIONS, fetched_at="2026-08-13T00:00:00Z")
    by_id = {r["model_id"]: r for r in recs}

    opus = by_id["claude-opus-4-1-20250805"]
    check("anthropic: opus-4-1 status retired", opus["status"] == "retired")
    check("anthropic: opus-4-1 retirement date", opus["retirement"] == "2026-08-05")
    check("anthropic: opus-4-1 announced from h3 id", opus["announced"] == "2026-06-05")
    check("anthropic: opus-4-1 replacement merged", opus["replacement"] == "claude-opus-4-8")

    fable = by_id["claude-fable-5"]
    check("anthropic: fable-5 active", fable["status"] == "active")
    check("anthropic: fable-5 no retirement date (bound only)",
          fable["retirement"] is None and fable["retirement_not_before"] == "2027-06-09")

    mythos = by_id["claude-mythos-preview"]
    check("anthropic: mythos preview deprecated (prose)", mythos["status"] == "deprecated")
    check("anthropic: mythos replacement", mythos["replacement"] == "claude-mythos-5")


# --- Anthropic models overview fixture --------------------------------------
ANTHROPIC_MODELS = """
<html><body>
<table><tbody>
<tr><td>Feature</td><td>Claude Fable 5</td><td>Claude Opus 5</td><td>Claude Haiku 4.5</td></tr>
<tr><td>Claude API ID</td><td>claude-fable-5</td><td>claude-opus-5</td><td>claude-haiku-4-5-20251001</td></tr>
<tr><td>Claude API alias</td><td>claude-fable-5</td><td>claude-opus-5</td><td>claude-haiku-4-5</td></tr>
<tr><td>AWS Bedrock ID</td><td>anthropic.claude-fable-5 3</td><td>anthropic.claude-opus-5 3</td><td>anthropic.claude-haiku-4-5-20251001-v1:0</td></tr>
</tbody></table>
</body></html>
"""


def test_anthropic_models():
    recs = P.parse_anthropic_models(ANTHROPIC_MODELS, fetched_at="2026-08-13T00:00:00Z")
    ids = [r["model_id"] for r in recs]
    check("anthropic models: 3 current ids", sorted(ids) == sorted(
        ["claude-fable-5", "claude-opus-5", "claude-haiku-4-5-20251001"]))
    check("anthropic models: family from header", any(
        r["model_id"] == "claude-haiku-4-5-20251001" and r["family"] == "Claude Haiku 4.5" for r in recs))
    check("anthropic models: no bedrock-style ids", not any("anthropic." in i for i in ids))


# --- OpenAI deprecations fixture --------------------------------------------
OPENAI_DEPRECATIONS = """
<html><body>
<p>2026-04-22: Legacy snapshot batch retirement</p>
<table><tbody>
<tr><td>Shutdown date</td><td>Model/system</td><td>Recommended replacement</td></tr>
<tr><td>January 20, 2026</td><td>gpt-4o-2024-05-13</td><td>gpt-4o-2024-11-20</td></tr>
<tr><td>2026-02-17</td><td>chatgpt-4o-latest</td><td>gpt-4o</td></tr>
<tr><td>October 23, 2026</td><td>Assistants API</td><td>Responses API</td></tr>
<tr><td>October 23, 2026</td><td>OpenAI-Beta: realtime=v1</td><td>gpt-5.6-realtime</td></tr>
<tr><td>October 23, 2026</td><td>New fine-tuning training on babbage-002</td><td>babbage-002</td></tr>
<tr><td>October 23, 2026</td><td>gpt-4.1-nano</td><td>gpt-5-nano</td></tr>
</tbody></table>
</body></html>
"""


def test_openai_deprecations():
    recs = P.parse_openai_deprecations(OPENAI_DEPRECATIONS, fetched_at="2026-08-13T00:00:00Z")
    ids = [r["model_id"] for r in recs]
    check("openai: model rows parsed", "gpt-4o-2024-05-13" in ids and "chatgpt-4o-latest" in ids)
    check("openai: API/system rows excluded",
          not any("assistants" in i.lower() or "beta" in i or " " in i for i in ids))
    check("openai: iso + us dates both parse",
          any(r["model_id"] == "gpt-4o-2024-05-13" and r["retirement"] == "2026-01-20" for r in recs)
          and any(r["model_id"] == "chatgpt-4o-latest" and r["retirement"] == "2026-02-17" for r in recs))
    check("openai: announcement from section header",
          any(r["model_id"] == "gpt-4o-2024-05-13" and r["announced"] == "2026-04-22" for r in recs))
    check("openai: descriptive cells excluded", "babbage-002" not in ids)


# --- OpenAI models fixture --------------------------------------------------
OPENAI_MODELS = """
<html><body>
<p>GPT-5.6 Sol. Frontier model for complex professional work. Model ID gpt-5.6-sol Alias gpt-5.6</p>
<p>GPT-5.6 Terra. Model ID gpt-5.6-terra</p>
</body></html>
"""


def test_openai_models():
    recs = P.parse_openai_models(OPENAI_MODELS, fetched_at="2026-08-13T00:00:00Z")
    ids = [r["model_id"] for r in recs]
    check("openai models: ids extracted", sorted(ids) == sorted(["gpt-5.6-sol", "gpt-5.6-terra"]))


# --- DeepSeek docs fixture --------------------------------------------------
DEEPSEEK_DOCS = """
<html><body>
<table><tbody><tr><td>model<sup>(1)</sup></td><td><code>deepseek-v4-flash</code><br><code>deepseek-v4-pro</code></td></tr></tbody></table>
<p>(1) The <code>deepseek-v4-flash</code> model has been
updated to DeepSeek-V4-Flash-0731, and the <code>deepseek-v4-pro</code> model has been updated to
DeepSeek-V4-Pro-0813. The calling method remains unchanged.</p>
</body></html>
"""


def test_deepseek_docs():
    recs = P.parse_deepseek_docs(DEEPSEEK_DOCS, fetched_at="2026-08-13T00:00:00Z")
    ids = [r["model_id"] for r in recs]
    check("deepseek: both current models", "deepseek-v4-flash" in ids and "deepseek-v4-pro" in ids)
    check("deepseek: dated snapshots captured", "deepseek-v4-flash-0731" in ids and "deepseek-v4-pro-0813" in ids)
    check("deepseek: update sentence as evidence", any(
        r["model_id"] == "deepseek-v4-flash" and "0731" in r["evidence"] for r in recs))


# --- October 2026 layouts -----------------------------------------------------
ANTHROPIC_DEPRECATIONS_OCT = """
<html><body>
<table><thead><tr><th>API model name</th><th>Current state</th><th>Deprecated</th><th>Tentative retirement date</th></tr></thead><tbody>
<tr><td>claude-mythos-preview</td><td>Deprecated</td><td>June 9, 2026</td><td>To be announced</td></tr>
<tr><td>claude-opus-5-5</td><td>Active</td><td>N/A</td><td>Not sooner than September 22, 2027</td></tr>
<tr><td>claude-sonnet-4-5-20250929</td><td>Deprecated</td><td>September 30, 2026</td><td>November 30, 2026</td></tr>
</tbody></table>
<h2 id="deprecation-history">Deprecation history</h2>
<h3 id="2026-09-30-claude-sonnet-4-5-model" class="group">2026-09-30: Claude Sonnet 4.5 model<span><button aria-label="Copy link"></button></span></h3>
<p>On September 30, 2026, Anthropic notified developers.</p>
<table><thead><tr><th>Retirement date</th><th>Deprecated model</th><th>Recommended replacement</th></tr></thead><tbody>
<tr><td>November 30, 2026</td><td><code>claude-sonnet-4-5-20250929</code></td><td><code>claude-sonnet-5-5</code></td></tr>
</tbody></table>
<h3 id="2024-09-04-claude-1-and-instant-models">2024-09-04: Claude 1 and Instant models</h3>
<table><thead><tr><th>Retirement date</th><th>Deprecated model</th><th>Recommended replacement</th></tr></thead><tbody>
<tr><td>November 6, 2024</td><td><code>claude-instant-1.2</code></td><td><code>claude-haiku-4-5-20251001</code></td></tr>
</tbody></table>
<h2 id="api-parameter-deprecations">API parameter deprecations</h2>
<table><thead><tr><th>Parameter</th><th>Status</th><th>Behavior</th><th>Recommended replacement</th></tr></thead><tbody>
<tr><td>temperature, top_p, top_k</td><td>Deprecated (Claude Opus 4.7 and later)</td><td>Returns a 400 error</td><td>Prompting</td></tr>
</tbody></table>
</body></html>
"""


def test_anthropic_deprecations_oct_layout():
    recs = P.parse_anthropic_deprecations(ANTHROPIC_DEPRECATIONS_OCT, fetched_at="2026-10-06T00:00:00Z")
    by_id = {r["model_id"]: r for r in recs}
    s45 = by_id["claude-sonnet-4-5-20250929"]
    check("anthropic oct: sonnet 4.5 deprecated (stated)", s45["status"] == "deprecated" and s45["stated_status"] == "Deprecated")
    check("anthropic oct: sonnet 4.5 dates", s45["announced"] == "2026-09-30" and s45["retirement"] == "2026-11-30")
    check("anthropic oct: replacement from h3-id history section", s45["replacement"] == "claude-sonnet-5-5")
    mp = by_id["claude-mythos-preview"]
    check("anthropic oct: mythos preview deprecated, retirement to be announced",
          mp["status"] == "deprecated" and mp["retirement"] is None and mp["announced"] == "2026-06-09")
    check("anthropic oct: opus 5.5 active with bound", by_id["claude-opus-5-5"]["retirement_not_before"] == "2027-09-22")
    check("anthropic oct: section bounded by next h3 (instant-1.2 dated)",
          by_id["claude-instant-1.2"]["announced"] == "2024-09-04" and by_id["claude-instant-1.2"]["retirement"] == "2024-11-06")
    check("anthropic oct: parameter table ignored", not any("temperature" in i for i in by_id))
    check("anthropic oct: undated id family joins version digits", by_id["claude-opus-5-5"]["family"] == "Claude Opus 5.5")


def test_anthropic_fails_closed():
    for label, html in [
        ("unknown state", ANTHROPIC_DEPRECATIONS_OCT.replace("<td>Deprecated</td><td>June 9", "<td>Legacy</td><td>June 9")),
        ("missing column", ANTHROPIC_DEPRECATIONS_OCT.replace("<th>Tentative retirement date</th>", "<th>Retires</th>")),
        ("no status table", "<html><body><p>moved</p></body></html>"),
    ]:
        try:
            P.parse_anthropic_deprecations(html)
            check(f"anthropic fails closed: {label}", False)
        except ValueError:
            check(f"anthropic fails closed: {label}", True)


DEEPSEEK_DOCS_OCT = """
<table><thead><tr><th>PARAM</th><th>VALUE</th></tr></thead><tbody>
<tr><td>model</td><td><code>deepseek-flash</code><sup>(1)</sup><br><code>deepseek-v4-pro</code></td></tr></tbody></table>
<div><p>(1) Use <code>deepseek-flash</code> as the model name. The legacy names <code>deepseek-v4-flash</code> and
<code>deepseek-v4-flash-vision-exp</code> are still accepted, but the corresponding models have been retired, their
requests are served by the DeepSeek-V4.1-Flash model and billed at the Flash price.</p></div>
"""


def test_deepseek_docs_oct_layout():
    recs = P.parse_deepseek_docs(DEEPSEEK_DOCS_OCT, fetched_at="2026-10-06T00:00:00Z")
    by_id = {r["model_id"]: r for r in recs}
    check("deepseek oct: current ids from model cell",
          by_id["deepseek-flash"]["kind"] == "current" and by_id["deepseek-v4-pro"]["kind"] == "current")
    for old in ("deepseek-v4-flash", "deepseek-v4-flash-vision-exp"):
        r = by_id[old]
        check(f"deepseek oct: {old} retired with no date, replacement deepseek-flash",
              r["kind"] == "lifecycle" and r["status"] == "retired" and r["retirement"] is None and r["replacement"] == "deepseek-flash")
    check("deepseek oct: serving model named", by_id["deepseek-v4.1-flash"]["family"] == "DeepSeek-V4.1-Flash")
    try:
        P.parse_deepseek_docs(DEEPSEEK_DOCS_OCT.replace("have been retired", "are being phased out"))
        check("deepseek fails closed on unknown footnote wording", False)
    except ValueError:
        check("deepseek fails closed on unknown footnote wording", True)


def test_real_captures_both_layouts():
    """Every capture in data/raw parses; old captures stay a regression test."""
    raw = Path(__file__).resolve().parent.parent / "data" / "raw"
    seen = {}
    for name, fn in P.PARSERS.items():
        files = sorted(raw.glob(f"{name}-*.html"))
        check(f"captures: {name} has old and new", len(files) >= 2)
        for f in files:
            seen[(name, f.name)] = {r["model_id"]: r for r in fn(f.read_text(encoding="utf-8"), fetched_at="x")}
    aug = {n: v for (n, f), v in seen.items() if "20260813" in f}
    octo = {n: v for (n, f), v in seen.items() if "20261006" in f}
    check("aug capture: deepseek-v4-flash current", aug["deepseek_docs"]["deepseek-v4-flash"]["kind"] == "current")
    check("aug capture: claude-3-opus retired via history", aug["anthropic_deprecations"]["claude-3-opus-20240229"]["status"] == "retired")
    check("oct capture: opus 5.5 and sonnet 5.5 listed",
          {"claude-opus-5-5", "claude-sonnet-5-5"} <= set(octo["anthropic_models"]))
    check("oct capture: deepseek-flash current, v4-flash retired",
          octo["deepseek_docs"]["deepseek-flash"]["kind"] == "current" and octo["deepseek_docs"]["deepseek-v4-flash"]["status"] == "retired")
    check("oct capture: sonnet 4.5 retirement 2026-11-30",
          octo["anthropic_deprecations"]["claude-sonnet-4-5-20250929"]["retirement"] == "2026-11-30")
    check("oct capture: GPT-6.1 Sol family", octo["openai_models"]["gpt-6.1-sol"]["family"] == "GPT-6.1 Sol")
    check("oct capture: overview family without tagline", octo["anthropic_models"]["claude-opus-5-5"]["family"] == "Claude Opus 5.5")


# --- Kimi platform fixture --------------------------------------------------
KIMI_PLATFORM = """
<html><body>
<a href="/docs/guide/kimi-k2-6-quickstart">K2.6</a>
<a href="/docs/guide/kimi-k3-quickstart">K3</a>
<a href="/console">console</a>
</body></html>
"""


def test_kimi_platform():
    recs = P.parse_kimi_platform(KIMI_PLATFORM, fetched_at="2026-08-13T00:00:00Z")
    ids = [r["model_id"] for r in recs]
    check("kimi: quickstart links parsed", sorted(ids) == sorted(["kimi-k2-6", "kimi-k3"]))
    check("kimi: non-model links ignored", "console" not in " ".join(ids))


if __name__ == "__main__":
    test_anthropic_deprecations()
    test_anthropic_models()
    test_openai_deprecations()
    test_openai_models()
    test_deepseek_docs()
    test_kimi_platform()
    test_anthropic_deprecations_oct_layout()
    test_anthropic_fails_closed()
    test_deepseek_docs_oct_layout()
    test_real_captures_both_layouts()
    print("ALL PASS")
