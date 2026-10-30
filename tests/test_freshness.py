"""Fixture tests for pipeline/freshness.py: agree (0), changed (1), incomplete (2)."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "pipeline"))
import freshness  # noqa: E402

RAW = PROJECT / "data" / "raw"
STAMP = "20261030T075912Z"
STORED = {
    source_id: (RAW / f"{source_id}-{STAMP}.html").read_bytes()
    for source_id, _url in freshness.SOURCES
}
# The published index was built on this date. An earlier clock puts shutdown
# statuses that are derived from the date back to deprecated.
PUBLISHED_DAY = datetime(2026, 10, 30, tzinfo=timezone.utc)
LOCAL = freshness.published_loader(str(PROJECT))


def fetcher(overrides: dict[str, bytes] | None = None, fail: set[str] = frozenset()):
    overrides = overrides or {}

    def fetch(source_id: str, url: str):
        if source_id in fail:
            raise RuntimeError("HTTP 503")
        expected = dict(freshness.SOURCES)[source_id]
        assert url == expected
        body = overrides.get(source_id, STORED[source_id])
        return body, {"transport": "fixture", "http_code": 200}

    return fetch


def check(fetch, loader=LOCAL, now: datetime = PUBLISHED_DAY):
    before = freshness.tree_digest(PROJECT)
    result = freshness.run_check(fetch, loader, project=PROJECT, published_label="fixture", now=now)
    assert result["source_tree_unchanged"] is True
    assert freshness.tree_digest(PROJECT) == before
    return result


def fields(result) -> set[str]:
    return {item["field"] for item in result["differences"]}


def test_identical_pages_agree_and_lifecycle_gap_stays_a_gap() -> None:
    result = check(fetcher())
    assert result["exit_code"] == 0, result["differences"] or result["incomplete"]
    assert result["sources_opened"] == result["sources_build_reads"] == 6
    assert result["quotes_checked"] >= 1
    gaps = {gap["vendor"]: gap for gap in result["documented_gaps"]}
    assert set(gaps) == {"DeepSeek", "Kimi"}
    assert gaps["DeepSeek"]["status"] == "documented_gap"
    assert gaps["DeepSeek"]["counts_as_opened"] is False
    assert gaps["DeepSeek"]["forces_incomplete"] is False
    assert gaps["Kimi"]["status"] == "documented_gap"
    assert result["informational"]["raw_bytes_changed_vs_stored"]["deepseek_docs"] is False


def test_html_comment_is_not_a_displayed_change() -> None:
    noisy = {source_id: b"<!-- freshness-noise -->" + body for source_id, body in STORED.items()}
    result = check(fetcher(noisy))
    assert result["exit_code"] == 0, result["differences"] or result["incomplete"]
    assert result["informational"]["raw_bytes_changed_vs_stored"]["anthropic_deprecations"] is True
    assert result["informational"]["raw_bytes_changed_vs_stored"]["kimi_platform"] is True
    assert {gap["status"] for gap in result["documented_gaps"]} == {"documented_gap"}


def test_renamed_model_is_reported() -> None:
    body = STORED["deepseek_docs"].replace(
        b"<code>deepseek-v4-pro</code>", b"<code>deepseek-v4-ultra</code>", 1
    )
    assert body != STORED["deepseek_docs"]
    result = check(fetcher({"deepseek_docs": body}))
    assert result["exit_code"] == 1
    assert "models.DeepSeek|deepseek-v4-pro|current" in fields(result)
    assert "models.DeepSeek|deepseek-v4-ultra|current" in fields(result)


def test_vanished_vendor_sentence_is_a_difference() -> None:
    body = STORED["deepseek_docs"].replace(b"<sup>(1)</sup>", b"", 1)
    start = body.find(b'<div style="font-size:14px"><p>(1) Use')
    end = body.find(b"</p></div>", start)
    assert start > 0 and end > start
    body = body[:start] + body[end + len(b"</p></div>") :]
    result = check(fetcher({"deepseek_docs": body}))
    assert result["exit_code"] == 1, result["incomplete"]
    assert any(field.startswith("quote absent:DeepSeek:") for field in fields(result))
    assert "models.DeepSeek|deepseek-v4-flash|lifecycle" in fields(result)


def test_clock_before_shutdown_changes_displayed_status() -> None:
    result = check(fetcher(), now=datetime(2026, 10, 7, tzinfo=timezone.utc))
    assert result["exit_code"] == 1, result["incomplete"]
    assert "models.OpenAI|gpt-4o-2024-05-13|lifecycle.status" in fields(result)
    # Sonnet 4.5's status is the word on the page. Moving the clock does not flip it.
    assert "models.Anthropic|claude-sonnet-4-5-20250929|lifecycle.status" not in fields(result)


def test_unreachable_source_is_incomplete_not_clean() -> None:
    result = check(fetcher(fail={"openai_models"}))
    assert result["exit_code"] == 2
    assert result["sources_opened"] == 5 and result["sources_build_reads"] == 6
    assert result["sources"]["openai_models"]["status"] == "unreachable"
    assert result["differences"] == []
    assert {gap["vendor"] for gap in result["documented_gaps"]} == {"DeepSeek", "Kimi"}
    assert {gap["status"] for gap in result["documented_gaps"]} == {"documented_gap"}


def test_unparseable_page_is_incomplete() -> None:
    body = STORED["deepseek_docs"].replace(b"<td>model</td>", b"<td>models</td>", 1)
    assert body != STORED["deepseek_docs"]
    result = check(fetcher({"deepseek_docs": body}))
    assert result["exit_code"] == 2
    assert any("scratch build failed" in reason for reason in result["incomplete"])
    assert result["differences"] == []


def test_unreadable_published_dataset_is_incomplete(tmp_path: Path) -> None:
    result = check(fetcher(), freshness.published_loader(str(tmp_path)))
    assert result["exit_code"] == 2
    assert any("published dataset could not be read" in reason for reason in result["incomplete"])


def test_cli_refuses_to_write_inside_the_project() -> None:
    with pytest.raises(SystemExit) as raised:
        freshness.main(["--output", str(PROJECT / "data" / "receipt.json")])
    assert raised.value.code == 2
