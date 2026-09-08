from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from trusted_rules import load_rules
from shadow import publish_validated_reports
from validate_report import (
    ReportError,
    blocking_failures,
    build_validation_state,
    load_schema,
    validate_report,
)


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "skills" / "validate-foundry-ci" / "references" / "default-rules.yaml"
SCHEMA_PATH = ROOT / "skills" / "validate-foundry-ci" / "references" / "report-schema.json"
REPORT_ID = "20260908T090000Z"


def _write_report(report_root: Path, mutate=None, expected_agent_root: Path | None = None) -> tuple[Path, Path]:
    expected_agent_root = expected_agent_root or report_root
    rules = load_rules(RULES_PATH)
    results = [
        {
            "ruleId": rule.rule_id,
            "title": rule.title,
            "level": rule.level,
            "status": "pass",
            "details": "Static repository evidence supports this result.",
            "guidance": list(rule.guidance),
        }
        for rule in rules
    ]
    report = {
        "reportId": REPORT_ID,
        "generatedAt": "2026-09-08T09:00:00Z",
        "target": {"serviceName": "agent", "agentRoot": str(expected_agent_root)},
        "results": results,
        "markdownPath": f".foundry/results/validation-{REPORT_ID}.md",
    }
    if mutate:
        mutate(report)
    result_dir = report_root / ".foundry" / "results"
    result_dir.mkdir(parents=True)
    json_path = result_dir / f"validation-{REPORT_ID}.json"
    markdown_path = result_dir / f"validation-{REPORT_ID}.md"
    json_path.write_text(json.dumps(report), encoding="utf-8")
    markdown_path.write_text(
        "# Microsoft Foundry Agent Validation\n\n"
        f"Report ID `{REPORT_ID}`\n\n"
        + "\n".join(f"### `{rule.rule_id}`: {rule.title}" for rule in rules),
        encoding="utf-8",
    )
    return json_path, markdown_path


def _validate(
    report_root: Path,
    json_path: Path,
    markdown_path: Path,
    expected_agent_root: Path | None = None,
):
    return validate_report(
        json_path,
        markdown_path,
        load_schema(SCHEMA_PATH),
        load_rules(RULES_PATH),
        report_root,
        expected_agent_root or report_root,
        "agent",
        REPORT_ID,
    )


def test_accepts_complete_semantic_report(tmp_path: Path) -> None:
    json_path, markdown_path = _write_report(tmp_path)
    report, counts = _validate(tmp_path, json_path, markdown_path)
    assert counts["pass"] == len(load_rules(RULES_PATH))
    assert blocking_failures(report) == []


def test_shadow_report_uses_original_target_metadata(tmp_path: Path) -> None:
    report_root = tmp_path / "shadow" / "evidence"
    original_root = tmp_path / "checkout"
    original_root.mkdir()
    json_path, markdown_path = _write_report(
        report_root,
        expected_agent_root=original_root,
    )
    report, _ = _validate(report_root, json_path, markdown_path, original_root)
    assert report["target"]["agentRoot"] == str(original_root)


def test_contradictory_model_markdown_is_replaced_before_publication(tmp_path: Path) -> None:
    report_root = tmp_path / "shadow" / "evidence"
    original_root = tmp_path / "checkout"
    original_root.mkdir()
    json_path, markdown_path = _write_report(
        report_root,
        expected_agent_root=original_root,
    )
    markdown_path.write_text(
        "# Contradictory model report\n\nPROTO-001 failed and the report ID is wrong.",
        encoding="utf-8",
    )
    report, counts = _validate(report_root, json_path, markdown_path, original_root)
    rendered = markdown_path.read_text(encoding="utf-8")
    assert "Contradictory model report" not in rendered
    assert f"| Report ID | `{REPORT_ID}` |" in rendered
    for result in report["results"]:
        assert f"### `{result['ruleId']}`: {result['title']}" in rendered
        assert f"- **Status:** {result['status']}" in rendered

    validation_state = tmp_path / "validation-state.json"
    publication_state = tmp_path / "publication-state.json"
    validation_state.write_text(
        json.dumps(build_validation_state(report, counts)),
        encoding="utf-8",
    )
    publish_validated_reports(
        report_root,
        original_root,
        REPORT_ID,
        validation_state,
        publication_state,
    )
    published_markdown = (
        original_root / ".foundry" / "results" / f"validation-{REPORT_ID}.md"
    ).read_text(encoding="utf-8")
    assert published_markdown == rendered
    assert "Contradictory model report" not in published_markdown


def test_rejects_empty_results(tmp_path: Path) -> None:
    json_path, markdown_path = _write_report(tmp_path, lambda report: report.update(results=[]))
    with pytest.raises(ReportError, match="exactly one"):
        _validate(tmp_path, json_path, markdown_path)


def test_rejects_duplicate_results(tmp_path: Path) -> None:
    def duplicate(report: dict) -> None:
        report["results"][1] = copy.deepcopy(report["results"][0])

    json_path, markdown_path = _write_report(tmp_path, duplicate)
    with pytest.raises(ReportError, match="Duplicate"):
        _validate(tmp_path, json_path, markdown_path)


@pytest.mark.parametrize("field", ["ruleId", "title", "level", "guidance"])
def test_rejects_modified_immutable_metadata(tmp_path: Path, field: str) -> None:
    def mutate(report: dict) -> None:
        value = report["results"][0][field]
        report["results"][0][field] = ["https://example.invalid"] if isinstance(value, list) else f"{value}-changed"

    json_path, markdown_path = _write_report(tmp_path, mutate)
    with pytest.raises(ReportError):
        _validate(tmp_path, json_path, markdown_path)


def test_rejects_extra_result_property(tmp_path: Path) -> None:
    def mutate(report: dict) -> None:
        report["results"][0]["unexpected"] = True

    json_path, markdown_path = _write_report(tmp_path, mutate)
    with pytest.raises(ReportError, match="additional"):
        _validate(tmp_path, json_path, markdown_path)


def test_rejects_schema_missing_required_property(tmp_path: Path) -> None:
    def mutate(report: dict) -> None:
        del report["generatedAt"]

    json_path, markdown_path = _write_report(tmp_path, mutate)
    with pytest.raises(ReportError, match="missing required"):
        _validate(tmp_path, json_path, markdown_path)


def test_only_warning_and_error_failures_block(tmp_path: Path) -> None:
    def mutate(report: dict) -> None:
        report["results"][0]["status"] = "fail"
        warning = next(result for result in report["results"] if result["level"] == "warning")
        warning["status"] = "fail"
        inconclusive = report["results"][1]
        inconclusive["status"] = "inconclusive"

    json_path, markdown_path = _write_report(tmp_path, mutate)
    report, _ = _validate(tmp_path, json_path, markdown_path)
    blocking = blocking_failures(report)
    assert report["results"][0]["ruleId"] not in blocking
    assert len(blocking) == 1
