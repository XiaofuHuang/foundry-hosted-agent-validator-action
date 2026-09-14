#!/usr/bin/env bash
set -euo pipefail

: "${AGENT_ROOT:?AGENT_ROOT must be set}"
: "${OUTPUT_ROOT:?OUTPUT_ROOT must be set}"
: "${PROMPT_FILE:?PROMPT_FILE must be set}"
: "${SKILL_ROOT:?SKILL_ROOT must be set}"

copilot_args=(
  -C "$AGENT_ROOT"
  --prompt "$(cat "$PROMPT_FILE")"
  --add-dir "$SKILL_ROOT"
  --add-dir "$OUTPUT_ROOT"
)
if [[ -n "${RULES_ROOT:-}" ]]; then
  copilot_args+=(--add-dir "$RULES_ROOT")
fi
copilot_args+=(
  --allow-tool=write
  --no-ask-user
  --no-auto-update
  --silent
)

copilot "${copilot_args[@]}"
