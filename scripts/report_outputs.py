from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path


class OutputError(ValueError):
    pass


def _write_output(path: Path, key: str, value: str) -> None:
    safe = value.replace("\r", " ").replace("\n", " ")
    with path.open("a", encoding="utf-8") as output:
        output.write(f"{key}={safe}\n")


def _regular_file(path: Path) -> bool:
    return path.is_absolute() and path.is_file() and not path.is_symlink()


def load_publication(path: Path) -> dict:
    state = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "version",
        "reportId",
        "conclusion",
        "summary",
        "counts",
        "blocking",
        "jsonReport",
        "markdownReport",
    }
    if not isinstance(state, dict) or set(state) != required:
        raise OutputError("Publication state is malformed")
    if state["version"] != 1 or state["conclusion"] not in {"success", "failure"}:
        raise OutputError("Publication state has invalid metadata")
    if not isinstance(state["reportId"], str) or not re.fullmatch(
        r"[0-9]{8}T[0-9]{6}Z", state["reportId"]
    ):
        raise OutputError("Publication report ID is invalid")
    if not isinstance(state["summary"], str) or not state["summary"]:
        raise OutputError("Publication summary is invalid")
    if not isinstance(state["counts"], dict) or set(state["counts"]) != {
        "pass",
        "fail",
        "inconclusive",
        "skipped",
    } or any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in state["counts"].values()
    ):
        raise OutputError("Publication counts are invalid")
    if not isinstance(state["blocking"], list) or any(
        not isinstance(rule_id, str) or not rule_id for rule_id in state["blocking"]
    ):
        raise OutputError("Publication blocking rules are invalid")
    if bool(state["blocking"]) != (state["conclusion"] == "failure"):
        raise OutputError("Publication conclusion contradicts blocking rules")
    if not isinstance(state["jsonReport"], str) or not isinstance(state["markdownReport"], str):
        raise OutputError("Publication paths are invalid")
    json_report = Path(state["jsonReport"])
    markdown_report = Path(state["markdownReport"])
    if not _regular_file(json_report) or not _regular_file(markdown_report):
        raise OutputError("Published reports are missing")
    expected_stem = f"validation-{state['reportId']}"
    if json_report.stem != expected_stem or markdown_report.stem != expected_stem:
        raise OutputError("Published report names do not match the report ID")
    if json_report.parent != markdown_report.parent:
        raise OutputError("Published reports do not share a results directory")
    if json_report.parent.name != "results" or json_report.parent.parent.name != ".foundry":
        raise OutputError("Published reports are outside .foundry/results")
    return state


def append_summary(path: Path, state: dict | None) -> None:
    if state is None:
        lines = [
            "## Microsoft Foundry hosted-agent validation",
            "",
            "**Conclusion:** failure",
            "",
            "The action failed before a validated report pair was published.",
        ]
    else:
        counts = state["counts"]
        total = sum(counts.values())
        lines = [
            "## Microsoft Foundry hosted-agent validation",
            "",
            f"**Conclusion:** {state['conclusion']}",
            "",
            "| Pass | Fail | Inconclusive | Skipped | Total |",
            "| ---: | ---: | ---: | ---: | ---: |",
            f"| {counts['pass']} | {counts['fail']} | {counts['inconclusive']} | "
            f"{counts['skipped']} | {total} |",
        ]
        if state["blocking"]:
            lines.extend(["", f"Blocking failed rules: {', '.join(state['blocking'])}"])
    with path.open("a", encoding="utf-8") as summary:
        summary.write("\n".join(lines) + "\n")


def emit_outputs(state_path: Path, github_output: Path, github_summary: Path) -> bool:
    try:
        state = load_publication(state_path)
    except (OSError, json.JSONDecodeError, OutputError, TypeError):
        _write_output(github_output, "json-report", "")
        _write_output(github_output, "markdown-report", "")
        _write_output(github_output, "conclusion", "failure")
        _write_output(
            github_output,
            "summary",
            "Action failed before validated reports were published",
        )
        append_summary(github_summary, None)
        return False

    _write_output(github_output, "json-report", os.fspath(state["jsonReport"]))
    _write_output(github_output, "markdown-report", os.fspath(state["markdownReport"]))
    _write_output(github_output, "conclusion", state["conclusion"])
    _write_output(github_output, "summary", state["summary"])
    append_summary(github_summary, state)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--github-output", required=True, type=Path)
    parser.add_argument("--github-summary", required=True, type=Path)
    args = parser.parse_args()
    return 0 if emit_outputs(args.state, args.github_output, args.github_summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
