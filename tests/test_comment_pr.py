from __future__ import annotations

import comment_pr
from comment_pr import (
    MARKER,
    MAX_COMMENT_BYTES,
    build_comment,
)


def test_build_comment_includes_report_without_mentions(tmp_path) -> None:
    report = tmp_path / "report.md"
    report.write_text("# Report\n\nNotify @octocat.", encoding="utf-8")

    body = build_comment(
        str(report),
        "success",
        "3 passed",
        "https://github.com/example/repo/actions/runs/1",
    )

    assert body.startswith(MARKER)
    assert "**Action result:** `success`" in body
    assert "**Summary:** 3 passed" in body
    assert "@\u200boctocat" in body


def test_build_comment_fails_closed_without_report() -> None:
    body = build_comment("", "failure", "", "https://example.test/run")
    assert "failed closed" in body
    assert "**Action result:** `failure`" in body


def test_comment_is_bounded_by_github_limit(tmp_path) -> None:
    report = tmp_path / "large.md"
    report.write_text("x" * 100_000, encoding="utf-8")
    body = build_comment(str(report), "success", "", "https://example.test/run")
    assert len(body.encode("utf-8")) <= MAX_COMMENT_BYTES
    assert "Report truncated" in body


def test_publish_comment_posts_without_listing_or_updating(monkeypatch) -> None:
    calls = []

    def fake_request(method, url, token, payload=None):
        calls.append((method, url, token, payload))
        return ({"id": 42}, 201)

    monkeypatch.setattr(comment_pr, "api_request", fake_request)
    comment_pr.publish_comment(
        "https://api.github.test",
        "owner/repo",
        7,
        "test-token",
        "report",
    )

    assert calls == [
        (
            "POST",
            "https://api.github.test/repos/owner/repo/issues/7/comments",
            "test-token",
            {"body": "report"},
        )
    ]
