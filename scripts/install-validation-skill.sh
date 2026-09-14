#!/usr/bin/env bash
set -euo pipefail

: "${RUNTIME_ROOT:?RUNTIME_ROOT must be set}"
: "${VALIDATION_REF:?VALIDATION_REF must be set}"
: "${GITHUB_ACTION_PATH:?GITHUB_ACTION_PATH must be set}"

source_repo="$RUNTIME_ROOT/github-copilot-for-azure"
skill_parent="$RUNTIME_ROOT/skills"
skill_root="$skill_parent/validate-foundry-ci"

git clone --depth 1 --filter=blob:none --sparse \
  --branch "$VALIDATION_REF" \
  https://github.com/microsoft/GitHub-Copilot-for-Azure.git "$source_repo"
git -C "$source_repo" sparse-checkout set \
  plugins/azure-skills/skills/microsoft-foundry/foundry-agent/validate
mkdir -p "$skill_root"
cp -R "$source_repo/plugins/azure-skills/skills/microsoft-foundry/foundry-agent/validate/." \
  "$skill_root/"
cp "$GITHUB_ACTION_PATH/skills/validate-foundry-ci/SKILL.md" "$skill_root/SKILL.md"
copilot skill add "$skill_parent"
