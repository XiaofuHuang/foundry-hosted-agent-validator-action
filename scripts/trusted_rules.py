from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


class RulesError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    rule_id: str
    title: str
    level: str
    guidance: tuple[str, ...]


def _plain_value(line: str, key: str) -> str:
    prefix = f"    {key}: "
    if not line.startswith(prefix):
        raise RulesError(f"Expected {key} in bundled rules")
    value = line[len(prefix) :].strip()
    if not value:
        raise RulesError(f"Empty {key} in bundled rules")
    return value


def load_rules(path: Path) -> list[Rule]:
    """Parse only immutable report metadata from the trusted bundled YAML shape."""
    lines = path.read_text(encoding="utf-8").splitlines()
    starts = [index for index, line in enumerate(lines) if line.startswith("  - id: ")]
    if not starts:
        raise RulesError("Bundled rules contain no rules")

    rules: list[Rule] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        block = lines[start:end]
        rule_id = block[0][len("  - id: ") :].strip()
        if not rule_id:
            raise RulesError("Bundled rule has an empty id")

        title_lines = [line for line in block if line.startswith("    title: ")]
        level_lines = [line for line in block if line.startswith("    level: ")]
        guidance_markers = [index for index, line in enumerate(block) if line == "    guidance:"]
        if len(title_lines) != 1 or len(level_lines) != 1 or len(guidance_markers) != 1:
            raise RulesError(f"Malformed immutable metadata for {rule_id}")

        guidance: list[str] = []
        for line in block[guidance_markers[0] + 1 :]:
            if line.startswith("      - "):
                encoded = line[len("      - ") :].strip()
                try:
                    value = json.loads(encoded)
                except json.JSONDecodeError as error:
                    raise RulesError(f"Malformed guidance for {rule_id}") from error
                if not isinstance(value, str) or not value.startswith(("https://", "http://")):
                    raise RulesError(f"Invalid guidance URI for {rule_id}")
                guidance.append(value)
            elif line.startswith("    "):
                break
        if not guidance or len(guidance) != len(set(guidance)):
            raise RulesError(f"Missing or duplicate guidance for {rule_id}")

        level = _plain_value(level_lines[0], "level")
        if level not in {"error", "warning", "recommendation"}:
            raise RulesError(f"Invalid level for {rule_id}")
        rules.append(
            Rule(
                rule_id=rule_id,
                title=_plain_value(title_lines[0], "title"),
                level=level,
                guidance=tuple(guidance),
            )
        )

    identifiers = [rule.rule_id for rule in rules]
    if len(identifiers) != len(set(identifiers)):
        raise RulesError("Bundled rules contain duplicate ids")
    return rules
