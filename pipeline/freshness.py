#!/usr/bin/env python3
"""Check whether the published model index still matches the vendor pages.

One command, no month argument (this tool has no monthly filings):

    python3 pipeline/freshness.py [--published-base URL|DIR] [--output receipt.json]

What it does:
  1. Re-fetches the six sources the build reads into a temporary directory.
     Stored captures are never touched.
  2. Rebuilds with this project's own build script in a scratch copy, so the
     fresh result is exactly what a refresh would publish. The scratch rebuild
     may accept a new semantic pin; that acceptance stays in the temp copy.
  3. Compares displayed model rows (identity, status, dates, replacement,
     evidence) and whether each vendor still has a lifecycle page. Receipt
     hashes, the capture timestamp, fetched_at, and the built date move on
     every rebuild and are not differences. Raw HTML churn is not a difference
     when those displayed values agree.
  4. A quote the published index cites, and that is present on the stored
     capture, must still be on the live page. Synthesized labels that were
     never on the page are not quotes. DeepSeek and Kimi publish no lifecycle
     page: that documented gap is reported, not counted as a failed fetch, and
     a repeated gap is not an incomplete check. If a rebuild starts publishing
     a lifecycle page for one of them, that is a difference.
  5. Exits 0 when nothing displayed differs, 1 when something does, and 2 when
     a source the build reads could not be fetched or parsed. Incomplete takes
     precedence.

The command never writes this project tree.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

PIPELINE = Path(__file__).resolve().parent
PROJECT = PIPELINE.parent
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))

import fetch as fetch_mod  # noqa: E402

LIVE_BASE = "https://greattombproductions.github.io/model-release-verifier/"
SOURCES = list(fetch_mod.SOURCES)
SOURCE_URLS = dict(SOURCES)
BUILD_TIMEOUT = 180
ROW_FIELDS = (
    "family",
    "status",
    "announced",
    "retirement",
    "retirement_not_before",
    "replacement",
    "evidence",
    "source_url",
)

Fetcher = Callable[[str, str], tuple[bytes, dict[str, Any]]]
Loader = Callable[[], bytes]


def norm_text(text: str) -> str:
    text = html.unescape(text)
    text = text.replace("\u2014", "-").replace("\u2013", "-").replace("\u2212", "-")
    text = text.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


def page_text(body: bytes) -> str:
    raw = body.decode("utf-8", errors="replace")
    raw = re.sub(r"<script\b.*?</script>", " ", raw, flags=re.S | re.I)
    raw = re.sub(r"<style\b.*?</style>", " ", raw, flags=re.S | re.I)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return norm_text(raw)


def _short(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 180 else text[:177] + "..."


def _row_key(row: dict) -> str:
    return f"{row.get('vendor')}|{row.get('model_id')}|{row.get('kind')}"


def displayed(data: dict) -> dict[str, Any]:
    """Values the page renders, minus fields that move on an unchanged rebuild."""
    models: dict[str, dict] = {}
    for row in data.get("models") or []:
        if not isinstance(row, dict):
            raise ValueError("model row is not an object")
        key = _row_key(row)
        if not row.get("vendor") or not row.get("model_id") or not row.get("kind"):
            raise ValueError(f"model row missing identity: {key}")
        if key in models:
            raise ValueError(f"duplicate model row {key}")
        models[key] = {field: row.get(field) for field in ROW_FIELDS}
    surfaces: dict[str, dict] = {}
    for vendor, coverage in (data.get("coverage") or {}).items():
        if not isinstance(coverage, dict):
            raise ValueError(f"coverage for {vendor} is not an object")
        surfaces[vendor] = {"has_lifecycle_page": bool(coverage.get("has_lifecycle_page"))}
    return {"models": models, "lifecycle_surface": surfaces}


def _walk(published: Any, fresh: Any, path: str, out: list[dict]) -> None:
    if published == fresh:
        return
    if isinstance(published, dict) and isinstance(fresh, dict):
        for key in sorted(set(published) | set(fresh)):
            _walk(published.get(key, "<absent>"), fresh.get(key, "<absent>"), f"{path}.{key}" if path else key, out)
        return
    if isinstance(published, list) and isinstance(fresh, list) and len(published) == len(fresh):
        for index, (left, right) in enumerate(zip(published, fresh)):
            _walk(left, right, f"{path}[{index}]", out)
        return
    out.append({"field": path, "published": published, "fresh": fresh})


def compare_displayed(published: dict, fresh: dict) -> list[dict]:
    differences: list[dict] = []
    _walk(displayed(published), displayed(fresh), "", differences)
    return differences


def tree_digest(project: Path) -> str:
    digest = hashlib.sha256()
    roots = [project / "data" / "raw", project / "data" / "generated", project / "pipeline", project / "frontend"]
    for root in roots:
        paths = sorted(path for path in root.rglob("*") if path.is_file()) if root.is_dir() else []
        for path in paths:
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            digest.update(str(path.relative_to(project)).encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def live_fetcher(source_id: str, url: str) -> tuple[bytes, dict[str, Any]]:
    del source_id
    body = fetch_mod.fetch(url)
    if not body or not body.strip():
        raise RuntimeError("empty body")
    return body, {"transport": "project-fetch", "http_code": None}


def published_loader(base: str) -> Loader:
    if re.match(r"^https?://", base):
        root = base if base.endswith("/") else base + "/"

        def load_remote() -> bytes:
            body, _meta = live_fetcher("published", root + "data/index.json")
            return body

        return load_remote

    directory = Path(base)

    def load_local() -> bytes:
        for relative in ("data/index.json", "data/generated/index.json"):
            candidate = directory / relative
            if candidate.is_file():
                return candidate.read_bytes()
        raise FileNotFoundError(f"data/index.json not found under {directory}")

    return load_local


def _stored_pages(project: Path) -> dict[str, str]:
    pages: dict[str, str] = {}
    raw = project / "data" / "raw"
    if not raw.is_dir():
        return pages
    for source_id, _url in SOURCES:
        files = sorted(raw.glob(f"{source_id}-*.html"))
        if files:
            pages[source_id] = page_text(files[-1].read_bytes())
    return pages


def _source_for_url(url: str) -> str | None:
    for source_id, source_url in SOURCES:
        if source_url == url:
            return source_id
    return None


def run_check(
    fetcher: Fetcher,
    loader: Loader,
    project: Path = PROJECT,
    published_label: str = LIVE_BASE,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    result: dict[str, Any] = {
        "tool": "Model Release Status Verifier",
        "checked_at": now.isoformat().replace("+00:00", "Z"),
        "published_baseline": published_label,
        "as_of": now.date().isoformat(),
        "scope": (
            "The six vendor pages the build reads, rebuilt with this project's build "
            "in a scratch tree and compared with the published index. No monthly dimension. "
            "Receipt hashes, the capture timestamp, fetched_at, and the built date are not "
            "differences. A quote cited from the stored capture must still be on the live page. "
            "DeepSeek and Kimi have no lifecycle page: that gap is reported, and a repeated "
            "gap is not an incomplete check. A lifecycle page appearing for either is a difference."
        ),
        "sources_build_reads": len(SOURCES),
        "sources_opened": 0,
        "sources": {},
        "documented_gaps": [],
        "quotes_checked": 0,
        "incomplete": [],
        "differences": [],
        "informational": {},
    }
    before = tree_digest(project)
    published_data = None
    try:
        published_data = json.loads(loader().decode("utf-8"))
    except Exception as error:
        result["incomplete"].append(f"published dataset could not be read: {error}")

    stored_pages = _stored_pages(project)
    with tempfile.TemporaryDirectory(prefix="model-release-freshness-") as temporary:
        scratch = Path(temporary) / "project"
        shutil.copytree(
            project / "pipeline",
            scratch / "pipeline",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        raw_dir = scratch / "data" / "raw"
        raw_dir.mkdir(parents=True)
        fresh_pages: dict[str, str] = {}
        fetched: dict[str, bytes] = {}
        stamp = now.strftime("%Y%m%dT%H%M%SZ")

        for source_id, url in SOURCES:
            entry: dict[str, Any] = {"url": url, "label": source_id}
            result["sources"][source_id] = entry
            try:
                body, meta = fetcher(source_id, url)
                if not body or not body.strip():
                    raise RuntimeError("empty body")
            except Exception as error:
                entry["status"] = "unreachable"
                entry["error"] = str(error)
                result["incomplete"].append(f"{source_id} could not be fetched: {error}")
                continue
            entry.update(meta)
            digest = hashlib.sha256(body).hexdigest()
            entry.update(bytes=len(body), raw_sha256=digest, status="fetched")
            result["sources_opened"] += 1
            stored = sorted((project / "data" / "raw").glob(f"{source_id}-*.html")) if (project / "data" / "raw").is_dir() else []
            if stored:
                entry["stored_capture"] = stored[-1].name
                entry["raw_bytes_match_stored_capture"] = stored[-1].read_bytes() == body
            filename = f"{source_id}-{stamp}.html"
            (raw_dir / filename).write_bytes(body)
            (raw_dir / f"{source_id}-{stamp}.sha256").write_text(
                f"{digest}  {filename}\n", encoding="utf-8"
            )
            fetched[source_id] = body
            fresh_pages[source_id] = page_text(body)

        fresh_data = None
        if len(fetched) == len(SOURCES):
            env = os.environ.copy()
            env["MODEL_RELEASE_AS_OF"] = now.date().isoformat()
            process = _build(scratch, env, accept=False)
            if process.returncode and "Vendor semantics changed" in (process.stderr or "") + (process.stdout or ""):
                process = _build(scratch, env, accept=True)
                result["informational"]["scratch_pin_accepted"] = process.returncode == 0
            if process.returncode:
                detail = (process.stderr or process.stdout).strip()[-800:]
                result["incomplete"].append(f"scratch build failed: {detail}")
            else:
                built = scratch / "data" / "generated" / "index.json"
                try:
                    fresh_data = json.loads(built.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as error:
                    result["incomplete"].append(f"scratch build wrote no index: {error}")
        else:
            result["incomplete"].append("scratch rebuild skipped: a source the build reads was not fetched")

        if fresh_data is not None and published_data is not None:
            try:
                result["differences"].extend(compare_displayed(published_data, fresh_data))
            except (KeyError, TypeError, ValueError) as error:
                result["incomplete"].append(f"published dataset layout not recognized: {error}")
            _gap_differences(published_data, fresh_data, result)
        elif published_data is not None:
            _record_gaps(published_data, None, result)

        if published_data is not None:
            seen: set[tuple[str, str]] = set()
            for row in published_data.get("models") or []:
                if not isinstance(row, dict):
                    continue
                evidence = row.get("evidence")
                source_id = _source_for_url(row.get("source_url") or "")
                if not evidence or not source_id or source_id not in fresh_pages:
                    continue
                quote = norm_text(str(evidence))
                stored = stored_pages.get(source_id, "")
                if not quote or quote not in stored:
                    continue
                key = (source_id, quote)
                if key in seen:
                    continue
                seen.add(key)
                result["quotes_checked"] += 1
                if quote not in fresh_pages[source_id]:
                    result["differences"].append({
                        "field": f"quote absent:{row.get('vendor')}:{row.get('model_id')}",
                        "published": evidence,
                        "fresh": "<absent from the live page>",
                    })

        result["informational"]["raw_bytes_changed_vs_stored"] = {
            source_id: not item.get("raw_bytes_match_stored_capture", True)
            for source_id, item in result["sources"].items()
            if "raw_sha256" in item
        }
        result["informational"]["not_compared"] = [
            "built",
            "models[].fetched_at",
            "coverage.*.surfaces.*.fetched_at",
            "coverage.*.surfaces.*.raw_sha256",
        ]

    after = tree_digest(project)
    result["source_tree_unchanged"] = before == after
    if before != after:
        result["incomplete"].append("project inputs or published data changed during the check (must never happen)")
    result["exit_code"] = 2 if result["incomplete"] else int(bool(result["differences"]))
    result["verdict"] = {
        0: "fresh: no published value differs from the live pages",
        1: "changed: published values differ from the live pages",
        2: "incomplete: the check could not establish agreement or difference",
    }[result["exit_code"]]
    return result


def _build(scratch: Path, env: dict[str, str], accept: bool) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(scratch / "pipeline" / "build.py")]
    if accept:
        command.append("--accept-source-change")
    return subprocess.run(
        command,
        cwd=scratch,
        capture_output=True,
        text=True,
        timeout=BUILD_TIMEOUT,
        check=False,
        env=env,
    )


def _record_gaps(published: dict, fresh: dict | None, result: dict[str, Any]) -> None:
    fresh_surfaces = {}
    if fresh is not None:
        try:
            fresh_surfaces = displayed(fresh)["lifecycle_surface"]
        except (TypeError, ValueError):
            fresh_surfaces = {}
    for vendor, coverage in (published.get("coverage") or {}).items():
        if not isinstance(coverage, dict) or coverage.get("has_lifecycle_page"):
            continue
        answered = bool(fresh_surfaces.get(vendor, {}).get("has_lifecycle_page"))
        result["documented_gaps"].append({
            "vendor": vendor,
            "status": "answered" if answered else "documented_gap",
            "counts_as_opened": False,
            "forces_incomplete": False,
            "note": (
                "the published gap is now stale"
                if answered
                else "no lifecycle page in the build's sources; a repeated gap is not an incomplete check"
            ),
        })


def _gap_differences(published: dict, fresh: dict, result: dict[str, Any]) -> None:
    _record_gaps(published, fresh, result)
    for gap in result["documented_gaps"]:
        if gap["status"] == "answered":
            result["differences"].append({
                "field": f"{gap['vendor']}:documented gap",
                "published": "no lifecycle page (documented gap)",
                "fresh": "lifecycle page present",
            })


def report(result: dict[str, Any]) -> None:
    print(f"Model Release Status Verifier freshness — {result['checked_at']}")
    print(f"Published baseline: {result['published_baseline']}")
    print(f"As of: {result['as_of']}")
    print(
        f"Opened {result['sources_opened']} of {result['sources_build_reads']} sources the build reads. "
        f"Quotes checked: {result['quotes_checked']}."
    )
    for source_id, entry in result["sources"].items():
        line = f"  {source_id}: {entry.get('status')} {entry.get('url', '')}"
        if "raw_sha256" in entry:
            same = entry.get("raw_bytes_match_stored_capture")
            line += f" ({entry['bytes']:,} bytes"
            line += ", raw bytes identical to stored capture)" if same else ", raw bytes differ from stored capture)"
        if entry.get("error"):
            line += f" — {entry['error'][:160]}"
        print(line)
    for gap in result["documented_gaps"]:
        print(f"  {gap['vendor']}: {gap['status']} — {gap['note']}")
    if result["differences"]:
        print(f"{len(result['differences'])} published value(s) differ:")
        for difference in result["differences"]:
            print(f"  {difference['field']}: published {_short(difference['published'])} -> live {_short(difference['fresh'])}")
    for reason in result["incomplete"]:
        print(f"INCOMPLETE: {reason}")
    print(f"Exit {result['exit_code']}: {result['verdict']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--published-base",
        default=LIVE_BASE,
        help="Published site URL (default: the live site) or a local directory "
        "(assembled site at data/index.json, or this source checkout)",
    )
    parser.add_argument("--output", type=Path, help="Write the full JSON receipt here (outside this project)")
    args = parser.parse_args(argv)
    if args.output is not None and _inside(args.output, PROJECT):
        parser.error(f"{args.output} is inside the project; the check never writes into its own inputs")
    try:
        result = run_check(live_fetcher, published_loader(args.published_base), published_label=args.published_base)
    except Exception as error:
        print(f"INCOMPLETE: check crashed: {error}")
        print("Exit 2: incomplete")
        return 2
    report(result)
    if args.output:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            print(f"Receipt: {args.output}")
        except OSError as error:
            print(f"INCOMPLETE: could not write receipt: {error}")
            return 2
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
