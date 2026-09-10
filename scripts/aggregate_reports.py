#!/usr/bin/env python3
"""Aggregate one validation invocation into the Action's shared output."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


class AggregationError(ValueError):
    """Raised when invocation output cannot be aggregated safely."""


def normalize_service_name(service_name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", service_name.lower()).strip("-")
    if not normalized:
        raise AggregationError(
            f"serviceName cannot be normalized for a report: {service_name!r}"
        )
    return normalized


def _load_state(state_path: Path, report_id: str) -> dict[str, Any]:
    if not state_path.exists():
        return {"reportId": report_id, "usedNames": [], "reportCount": 0}
    if state_path.is_symlink() or not state_path.is_file():
        raise AggregationError(f"Aggregation state is not a regular file: {state_path}")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AggregationError(f"Cannot read aggregation state: {error}") from error
    if not isinstance(state, dict) or state.get("reportId") != report_id:
        raise AggregationError("Aggregation state has a mismatched reportId")
    used_names = state.get("usedNames")
    report_count = state.get("reportCount")
    if (
        not isinstance(used_names, list)
        or any(not isinstance(name, str) or not name for name in used_names)
        or len(set(used_names)) != len(used_names)
        or not isinstance(report_count, int)
        or isinstance(report_count, bool)
        or report_count != len(used_names)
    ):
        raise AggregationError("Aggregation state is malformed")
    return state


def _regular_files(directory: Path, pattern: str) -> list[Path]:
    files = sorted(directory.glob(pattern), key=lambda path: path.name)
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise AggregationError(f"Invocation output is not a regular file: {path}")
    return files


def _validate_pairs(
    source: Path, report_id: str
) -> list[tuple[Path, Path, dict[str, Any]]]:
    json_files = _regular_files(source, "validation-*.json")
    markdown_files = _regular_files(source, "validation-*.md")
    json_by_stem = {path.stem: path for path in json_files}
    markdown_by_stem = {path.stem: path for path in markdown_files}
    if len(json_by_stem) != len(json_files) or len(markdown_by_stem) != len(
        markdown_files
    ):
        raise AggregationError("Invocation output contains duplicate report paths")
    if json_by_stem.keys() != markdown_by_stem.keys():
        missing_markdown = sorted(json_by_stem.keys() - markdown_by_stem.keys())
        missing_json = sorted(markdown_by_stem.keys() - json_by_stem.keys())
        raise AggregationError(
            "Invocation output contains incomplete report pairs "
            f"(missing Markdown: {missing_markdown}; missing JSON: {missing_json})"
        )

    expected_prefix = f"validation-{report_id}-"
    pairs: list[tuple[Path, Path, dict[str, Any]]] = []
    for stem, json_path in json_by_stem.items():
        if not stem.startswith(expected_prefix) or stem == expected_prefix:
            raise AggregationError(
                f"Report filename does not use reportId {report_id}: {json_path.name}"
            )
        markdown_path = markdown_by_stem[stem]
        try:
            report = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise AggregationError(f"Cannot parse report JSON {json_path}: {error}") from error
        if not isinstance(report, dict):
            raise AggregationError(f"Report JSON must be an object: {json_path}")
        if report.get("reportId") != report_id:
            raise AggregationError(f"Report JSON has a mismatched reportId: {json_path}")
        target = report.get("target")
        if not isinstance(target, dict) or not isinstance(
            target.get("serviceName"), str
        ):
            raise AggregationError(
                f"Report JSON has no string target.serviceName: {json_path}"
            )
        if not isinstance(target.get("agentRoot"), str):
            raise AggregationError(
                f"Report JSON has no string target.agentRoot: {json_path}"
            )
        declared_markdown = report.get("markdownPath")
        if not isinstance(declared_markdown, str):
            raise AggregationError(f"Report JSON has no string markdownPath: {json_path}")
        if Path(declared_markdown).resolve() != markdown_path.resolve():
            raise AggregationError(
                f"Report JSON markdownPath does not match its pair: {json_path}"
            )
        pairs.append((json_path, markdown_path, report))

    # validate.md writes each agent transaction serially, so JSON write time
    # preserves its service-key order within one invocation.
    pairs.sort(key=lambda pair: (pair[0].stat().st_mtime_ns, pair[0].name))
    return pairs


def _next_name(base: str, used_names: set[str]) -> str:
    if base not in used_names:
        return base
    suffix = 1
    while f"{base}-{suffix}" in used_names:
        suffix += 1
    return f"{base}-{suffix}"


def _rewrite_markdown_agent_root(
    markdown_path: Path, original_agent_root: str, stable_agent_root: str
) -> bytes:
    try:
        markdown = markdown_path.read_bytes()
        original = original_agent_root.encode("utf-8")
        stable = stable_agent_root.encode("utf-8")
    except (OSError, UnicodeError) as error:
        raise AggregationError(
            f"Cannot read report Markdown {markdown_path}: {error}"
        ) from error

    prefix = b"**Agent root:** "
    suffix = b"<br>"
    lines = markdown.splitlines(keepends=True)
    matches: list[tuple[int, bytes, bytes]] = []
    for index, line in enumerate(lines):
        content = line.rstrip(b"\r\n")
        ending = line[len(content) :]
        if content.startswith(prefix):
            matches.append((index, content, ending))
    if len(matches) != 1:
        raise AggregationError(
            f"Report Markdown must contain exactly one Agent root field: {markdown_path}"
        )

    index, content, ending = matches[0]
    if content != prefix + original + suffix:
        raise AggregationError(
            f"Report Markdown Agent root does not match its JSON pair: {markdown_path}"
        )
    lines[index] = prefix + stable + suffix + ending
    return b"".join(lines)


def aggregate_invocation(
    source: Path,
    output: Path,
    state_path: Path,
    report_id: str,
    agent_root: str,
) -> int:
    source = source.resolve()
    output = output.resolve()
    state_path = state_path.resolve()
    if source == output:
        raise AggregationError("Invocation and aggregate output directories must differ")
    if not source.is_dir():
        raise AggregationError(f"Invocation output directory does not exist: {source}")
    if (
        not agent_root
        or agent_root in {".", "./"}
        or "\n" in agent_root
        or "\r" in agent_root
    ):
        raise AggregationError("Stable agent root must identify the original workspace")

    state = _load_state(state_path, report_id)
    pairs = _validate_pairs(source, report_id)
    expected_rules_name = f"agent-validation-{report_id}-rules.yaml"
    rules_files = _regular_files(source, "agent-validation-*-rules.yaml")
    if len(rules_files) > 1:
        raise AggregationError("Invocation output contains multiple merged-rules files")
    if rules_files and rules_files[0].name != expected_rules_name:
        raise AggregationError(
            f"Merged-rules filename does not use reportId {report_id}: "
            f"{rules_files[0].name}"
        )
    if pairs and not rules_files:
        raise AggregationError("Invocation reports have no merged-rules artifact")

    final_rules = output / expected_rules_name
    source_rules = rules_files[0] if rules_files else None
    if source_rules and final_rules.exists():
        if final_rules.is_symlink() or not final_rules.is_file():
            raise AggregationError(
                f"Aggregate merged-rules path is not a regular file: {final_rules}"
            )
        if source_rules.read_bytes() != final_rules.read_bytes():
            raise AggregationError("Invocation merged rules differ from earlier rules")
    elif not source_rules and pairs:
        raise AggregationError("Invocation reports have no merged-rules artifact")

    output.mkdir(parents=True, exist_ok=True)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    used_names = set(state["usedNames"])
    planned: list[tuple[dict[str, Any], bytes, str]] = []
    for json_path, markdown_path, report in pairs:
        base = normalize_service_name(report["target"]["serviceName"])
        final_name = _next_name(base, used_names)
        used_names.add(final_name)
        final_json = output / f"validation-{report_id}-{final_name}.json"
        final_markdown = output / f"validation-{report_id}-{final_name}.md"
        if final_json.exists() or final_markdown.exists():
            raise AggregationError(
                f"Aggregate report path already exists for serviceName {final_name}"
            )
        rewritten = dict(report)
        rewritten_target = dict(report["target"])
        rewritten_target["agentRoot"] = agent_root
        rewritten["target"] = rewritten_target
        rewritten["markdownPath"] = str(final_markdown.resolve())
        rewritten_markdown = _rewrite_markdown_agent_root(
            markdown_path, report["target"]["agentRoot"], agent_root
        )
        planned.append((rewritten, rewritten_markdown, final_name))

    staging = Path(tempfile.mkdtemp(prefix=".aggregate-", dir=output))
    created: list[Path] = []
    try:
        staged_rules = None
        if source_rules and not final_rules.exists():
            staged_rules = staging / expected_rules_name
            shutil.copyfile(source_rules, staged_rules)

        staged_pairs: list[tuple[Path, Path, Path, Path]] = []
        for report, markdown, final_name in planned:
            final_json = output / f"validation-{report_id}-{final_name}.json"
            final_markdown = output / f"validation-{report_id}-{final_name}.md"
            staged_json = staging / final_json.name
            staged_markdown = staging / final_markdown.name
            staged_json.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            staged_markdown.write_bytes(markdown)
            staged_pairs.append(
                (staged_json, final_json, staged_markdown, final_markdown)
            )

        if staged_rules:
            os.replace(staged_rules, final_rules)
            created.append(final_rules)
        for staged_json, final_json, staged_markdown, final_markdown in staged_pairs:
            os.replace(staged_json, final_json)
            created.append(final_json)
            os.replace(staged_markdown, final_markdown)
            created.append(final_markdown)

        next_state = {
            "reportId": report_id,
            "usedNames": state["usedNames"] + [item[2] for item in planned],
            "reportCount": state["reportCount"] + len(planned),
        }
        state_temp = state_path.with_name(f".{state_path.name}.tmp")
        state_temp.write_text(
            json.dumps(next_state, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(state_temp, state_path)
    except (OSError, UnicodeError) as error:
        for path in reversed(created):
            try:
                path.unlink()
            except OSError:
                pass
        raise AggregationError(f"Cannot commit invocation output: {error}") from error
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return len(planned)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--report-id", required=True)
    parser.add_argument("--agent-root", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        report_count = aggregate_invocation(
            args.source, args.output, args.state, args.report_id, args.agent_root
        )
    except AggregationError as error:
        print(f"Report aggregation failed: {error}", file=sys.stderr)
        return 1
    print(report_count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
