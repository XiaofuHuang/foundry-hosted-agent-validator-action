from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from trusted_rules import Rule, load_rules


class ReportError(ValueError):
    pass


REPORT_KEYS = {"reportId", "generatedAt", "target", "results", "markdownPath"}
RESULT_KEYS = {"ruleId", "title", "level", "status", "details", "guidance"}
STATUSES = {"pass", "fail", "inconclusive", "skipped"}


def _validate_schema(value: object, schema: dict, location: str = "$") -> None:
    expected_type = schema.get("type")
    type_matches = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
    }
    if expected_type in type_matches and not type_matches[expected_type]:
        raise ReportError(f"{location} must have JSON type {expected_type}")

    if "enum" in schema and value not in schema["enum"]:
        raise ReportError(f"{location} is not an allowed value")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise ReportError(f"{location} is missing required properties")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            additional = set(value) - set(properties)
            if additional:
                raise ReportError(f"{location} has additional properties")
        for key, child in value.items():
            if key in properties:
                _validate_schema(child, properties[key], f"{location}.{key}")

    if isinstance(value, list):
        minimum = schema.get("minItems")
        if isinstance(minimum, int) and len(value) < minimum:
            raise ReportError(f"{location} has too few items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True) for item in value]
            if len(encoded) != len(set(encoded)):
                raise ReportError(f"{location} has duplicate items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, child in enumerate(value):
                _validate_schema(child, item_schema, f"{location}[{index}]")

    if isinstance(value, str):
        minimum = schema.get("minLength")
        if isinstance(minimum, int) and len(value) < minimum:
            raise ReportError(f"{location} is too short")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            raise ReportError(f"{location} does not match its required pattern")
        if schema.get("format") == "date-time":
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as error:
                raise ReportError(f"{location} is not an ISO date-time") from error
            if parsed.tzinfo is None:
                raise ReportError(f"{location} date-time must include a timezone")
        if schema.get("format") == "uri":
            parsed_uri = urlparse(value)
            if parsed_uri.scheme not in {"http", "https"} or not parsed_uri.netloc:
                raise ReportError(f"{location} is not an absolute HTTP(S) URI")


def load_schema(path: Path) -> dict:
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReportError("Bundled report schema is not valid JSON") from error
    if not isinstance(schema, dict):
        raise ReportError("Bundled report schema must be an object")
    return schema


def _require_exact_keys(value: object, keys: set[str], label: str) -> dict:
    if not isinstance(value, dict):
        raise ReportError(f"{label} must be an object")
    if set(value) != keys:
        raise ReportError(f"{label} has missing or additional properties")
    return value


def _require_string(value: object, label: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        raise ReportError(f"{label} must be a{' non-empty' if nonempty else ''} string")
    return value


def _safe_regular_file(path: Path, expected_parent: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ReportError(f"{path.name} is missing or is not a regular file")
    if path.stat().st_size <= 0 or path.stat().st_size > 10 * 1024 * 1024:
        raise ReportError(f"{path.name} has an invalid size")
    if path.resolve().parent != expected_parent.resolve():
        raise ReportError(f"{path.name} resolves outside the results directory")


def render_markdown(report: dict) -> str:
    target = report["target"]
    lines = [
        "# Microsoft Foundry Agent Validation",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Report ID | `{report['reportId']}` |",
        f"| Service | {target['serviceName']} |",
        f"| Hosted Agent Root | {target['agentRoot']} |",
        f"| Generated | {report['generatedAt']} |",
        "",
        "## Rule results",
        "",
    ]
    for result in report["results"]:
        guidance = ", ".join(f"[{url}]({url})" for url in result["guidance"])
        lines.extend(
            [
                f"### `{result['ruleId']}`: {result['title']}",
                "",
                f"- **Level:** {result['level']}",
                f"- **Status:** {result['status']}",
                f"- **Guidance:** {guidance}",
                "",
                "#### Details",
                "",
                result["details"],
                "",
            ]
        )
    lines.extend(
        [
            "## Limitation",
            "",
            "This is an automated, repository-based best-practice review. It is not Microsoft "
            "certification, a compliance attestation, penetration testing, or validation of the "
            "deployed Azure environment.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_state(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def validate_report(
    json_path: Path,
    markdown_path: Path,
    report_schema: dict,
    rules: list[Rule],
    report_root: Path,
    expected_agent_root: Path,
    service_name: str,
    report_id: str,
) -> tuple[dict, Counter]:
    expected_results = report_root / ".foundry" / "results"
    expected_json = expected_results / f"validation-{report_id}.json"
    expected_markdown = expected_results / f"validation-{report_id}.md"
    if json_path.absolute() != expected_json.absolute() or markdown_path.absolute() != expected_markdown.absolute():
        raise ReportError("Report paths are not canonical")
    _safe_regular_file(json_path, expected_results)
    if markdown_path.is_symlink() or (markdown_path.exists() and not markdown_path.is_file()):
        raise ReportError("Markdown report path is not a regular file")
    if markdown_path.parent.resolve() != expected_results.resolve():
        raise ReportError("Markdown report path resolves outside the results directory")

    try:
        report = json.loads(json_path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ReportError("JSON report is not valid UTF-8 JSON") from error
    _validate_schema(report, report_schema)
    report = _require_exact_keys(report, REPORT_KEYS, "report")
    if _require_string(report["reportId"], "reportId") != report_id or not re.fullmatch(
        r"[0-9]{8}T[0-9]{6}Z", report_id
    ):
        raise ReportError("reportId does not match the reserved canonical ID")
    generated_at = _require_string(report["generatedAt"], "generatedAt")
    try:
        parsed_time = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ReportError("generatedAt is not an ISO date-time") from error
    if parsed_time.tzinfo is None:
        raise ReportError("generatedAt must include a timezone")

    target = _require_exact_keys(report["target"], {"serviceName", "agentRoot"}, "target")
    if _require_string(target["serviceName"], "target.serviceName", nonempty=False) != service_name:
        raise ReportError("target.serviceName does not match static target selection")
    if _require_string(target["agentRoot"], "target.agentRoot") != os.fspath(expected_agent_root):
        raise ReportError("target.agentRoot does not match the selected target")
    expected_markdown_relative = f".foundry/results/validation-{report_id}.md"
    if _require_string(report["markdownPath"], "markdownPath") != expected_markdown_relative:
        raise ReportError("markdownPath is not canonical")

    results = report["results"]
    if not isinstance(results, list) or len(results) != len(rules):
        raise ReportError("results must contain exactly one entry per bundled rule")
    seen: set[str] = set()
    counts: Counter = Counter()
    for index, (result_value, rule) in enumerate(zip(results, rules, strict=True)):
        result = _require_exact_keys(result_value, RESULT_KEYS, f"results[{index}]")
        rule_id = _require_string(result["ruleId"], f"results[{index}].ruleId")
        if rule_id in seen:
            raise ReportError(f"Duplicate result for rule {rule_id}")
        seen.add(rule_id)
        if rule_id != rule.rule_id:
            raise ReportError("Rule results are missing, additional, or out of order")
        if _require_string(result["title"], f"{rule_id}.title") != rule.title:
            raise ReportError(f"Immutable title changed for {rule_id}")
        if _require_string(result["level"], f"{rule_id}.level") != rule.level:
            raise ReportError(f"Immutable level changed for {rule_id}")
        guidance = result["guidance"]
        if not isinstance(guidance, list) or tuple(guidance) != rule.guidance:
            raise ReportError(f"Immutable guidance changed for {rule_id}")
        status = _require_string(result["status"], f"{rule_id}.status")
        if status not in STATUSES:
            raise ReportError(f"Invalid status for {rule_id}")
        _require_string(result["details"], f"{rule_id}.details")
        counts[status] += 1

    markdown_path.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    return report, counts


def blocking_failures(report: dict) -> list[str]:
    return [
        result["ruleId"]
        for result in report["results"]
        if result["level"] in {"error", "warning"} and result["status"] == "fail"
    ]


def build_validation_state(report: dict, counts: Counter) -> dict:
    blocking = blocking_failures(report)
    conclusion = "failure" if blocking else "success"
    summary = (
        f"{counts['pass']} passed, {counts['fail']} failed, "
        f"{counts['inconclusive']} inconclusive, {counts['skipped']} skipped"
    )
    return {
        "version": 1,
        "reportId": report["reportId"],
        "conclusion": conclusion,
        "summary": summary,
        "counts": {status: counts[status] for status in sorted(STATUSES)},
        "blocking": blocking,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-report", required=True, type=Path)
    parser.add_argument("--markdown-report", required=True, type=Path)
    parser.add_argument("--schema", required=True, type=Path)
    parser.add_argument("--rules", required=True, type=Path)
    parser.add_argument("--report-root", required=True, type=Path)
    parser.add_argument("--expected-agent-root", required=True, type=Path)
    parser.add_argument("--service-name", required=True)
    parser.add_argument("--report-id", required=True)
    parser.add_argument("--model-outcome", required=True)
    parser.add_argument("--state", required=True, type=Path)
    args = parser.parse_args()

    counts: Counter = Counter()
    try:
        args.state.unlink(missing_ok=True)
        if args.model_outcome != "success":
            raise ReportError("Copilot validation invocation did not complete successfully")
        rules = load_rules(args.rules)
        report, counts = validate_report(
            args.json_report,
            args.markdown_report,
            load_schema(args.schema),
            rules,
            args.report_root,
            args.expected_agent_root,
            args.service_name,
            args.report_id,
        )
        state = build_validation_state(report, counts)
        blocking = state["blocking"]
        _write_state(args.state, state)
        if blocking:
            print(f"::error::Blocking validation rules failed: {', '.join(blocking)}")
            return 2
        return 0
    except (OSError, ReportError, ValueError) as error:
        message = str(error).replace("%", "%25").replace("\r", " ").replace("\n", " ")
        print(f"::error::Report validation failed closed: {message}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
