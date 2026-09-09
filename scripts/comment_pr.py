from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


MARKER = "<!-- foundry-hosted-agent-validation -->"
MAX_COMMENT_BYTES = 65_000
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def truncate_utf8(value: str, maximum: int = MAX_COMMENT_BYTES) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= maximum:
        return value
    suffix = "\n\n> Report truncated. Download the workflow artifact for the complete report."
    budget = maximum - len(suffix.encode("utf-8"))
    return encoded[:budget].decode("utf-8", errors="ignore") + suffix


def build_comment(
    report_path: str,
    conclusion: str,
    summary: str,
    run_url: str,
) -> str:
    sections = [
        MARKER,
        "## Microsoft Foundry hosted-agent validation",
        "",
        f"**Action result:** `{conclusion or 'failure'}`",
    ]
    if summary:
        sections.extend(["", f"**Summary:** {summary}"])
    sections.extend(["", f"[View workflow run]({run_url})", ""])

    path = Path(report_path) if report_path else None
    if path and path.is_file() and not path.is_symlink():
        report = path.read_text(encoding="utf-8").replace("@", "@\u200b")
        sections.append(report)
    else:
        sections.append(
            "> The validator failed closed before producing a publishable report."
        )
    return truncate_utf8("\n".join(sections))


def api_request(
    method: str,
    url: str,
    token: str,
    payload: dict[str, str] | None = None,
) -> tuple[Any, int]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    authorization_header = " ".join(("Bearer", token))
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": authorization_header,
            "Content-Type": "application/json",
            "User-Agent": "foundry-hosted-agent-validator-action",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read()
            return (json.loads(content) if content else None, response.status)
    except urllib.error.HTTPError as error:
        raise RuntimeError(
            f"GitHub API request failed with HTTP {error.code}"
        ) from error


def publish_comment(
    api_url: str,
    repository: str,
    pr_number: int,
    token: str,
    body: str,
) -> None:
    base = api_url.rstrip("/")
    _, status = api_request(
        "POST",
        f"{base}/repos/{repository}/issues/{pr_number}/comments",
        token,
        {"body": body},
    )
    if status != 201:
        raise RuntimeError(f"Unexpected GitHub comment status: {status}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--token", required=True)
    parser.add_argument("--report-path", default="")
    parser.add_argument("--conclusion", default="failure")
    parser.add_argument("--summary", default="")
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--api-url", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not REPOSITORY_PATTERN.fullmatch(args.repository):
        raise SystemExit("repository must use owner/name format")
    if args.pr_number <= 0:
        raise SystemExit("pr-number must be positive")
    if not args.token:
        raise SystemExit("token must not be empty")
    body = build_comment(
        args.report_path,
        args.conclusion,
        args.summary,
        args.run_url,
    )
    publish_comment(
        args.api_url,
        args.repository,
        args.pr_number,
        args.token,
        body,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
