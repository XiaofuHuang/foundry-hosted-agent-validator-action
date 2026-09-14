#!/usr/bin/env bash
set -euo pipefail

: "${SOURCE_REPO:?SOURCE_REPO must be set}"
: "${SKILL_PARENT:?SKILL_PARENT must be set}"
: "${SKILL_ROOT:?SKILL_ROOT must be set}"
: "${VALIDATION_REF:?VALIDATION_REF must be set}"
: "${GITHUB_ACTION_PATH:?GITHUB_ACTION_PATH must be set}"

git clone --depth 1 --filter=blob:none --sparse \
  --branch "$VALIDATION_REF" \
  https://github.com/microsoft/GitHub-Copilot-for-Azure.git "$SOURCE_REPO"
git -C "$SOURCE_REPO" sparse-checkout set \
  plugins/azure-skills/skills/microsoft-foundry/foundry-agent/validate
mkdir -p "$SKILL_ROOT"
cp -R "$SOURCE_REPO/plugins/azure-skills/skills/microsoft-foundry/foundry-agent/validate/." \
  "$SKILL_ROOT/"
cp "$GITHUB_ACTION_PATH/skills/validate-foundry-ci/SKILL.md" "$SKILL_ROOT/SKILL.md"
copilot skill add "$SKILL_PARENT"
