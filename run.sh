#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${INPUT_GITHUB_TOKEN:-}" ]]; then
  echo "::error::github-token must not be empty"
  exit 1
fi

export GITHUB_TOKEN="$INPUT_GITHUB_TOKEN"
export GH_TOKEN="$INPUT_GITHUB_TOKEN"
posted_comments=0
permissions_locked=false
permissions_file="$RUNNER_TEMP/foundry-validation-permissions"

post_comment() {
  local body_file="$1"
  if [[ "${GITHUB_EVENT_NAME:-}" != "pull_request" ]]; then
    return 0
  fi

  local pr_number
  pr_number="$(jq -r '.pull_request.number // empty' "$GITHUB_EVENT_PATH")"
  if [[ -z "$pr_number" ]]; then
    echo "::error::Pull request number is missing from the event payload"
    return 1
  fi

  if ! jq -Rs '{body: .}' "$body_file" |
    gh api --method POST \
      "repos/$GITHUB_REPOSITORY/issues/$pr_number/comments" \
      --input - > /dev/null; then
    return 1
  fi
  posted_comments=$((posted_comments + 1))
}

restore_permissions() {
  if [[ "$permissions_locked" != "true" || ! -f "$permissions_file" ]]; then
    return 0
  fi
  while IFS= read -r -d '' entry; do
    mode="${entry%% *}"
    path="${entry#* }"
    chmod "$mode" "$path"
  done < "$permissions_file"
  permissions_locked=false
}

finish() {
  local validation_status="$?"
  trap - EXIT
  set +e
  restore_permissions
  local restore_status="$?"
  if [[ "$validation_status" -eq 0 && "$restore_status" -ne 0 ]]; then
    validation_status="$restore_status"
  fi
  if [[ "$validation_status" -ne 0 && "$posted_comments" -eq 0 ]]; then
    failure_comment="$RUNNER_TEMP/foundry-validation-failure.md"
    {
      echo "## Microsoft Foundry hosted-agent validation"
      echo
      echo "**Validation failed before any report was produced.**"
      echo
      echo "[View workflow run]($GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID)"
    } > "$failure_comment"
    post_comment "$failure_comment"
  fi
  exit "$validation_status"
}
trap finish EXIT

workspace="$(realpath "$GITHUB_WORKSPACE")"
validate_root="$(realpath -m "$workspace/${INPUT_VALIDATE_PATH:-.}")"
case "$validate_root/" in
  "$workspace/"*) ;;
  *)
    echo "::error::validate-path must stay inside GITHUB_WORKSPACE"
    exit 1
    ;;
esac
if [[ ! -d "$validate_root" ]]; then
  echo "::error::validate-path must be a directory"
  exit 1
fi

export COPILOT_HOME="$RUNNER_TEMP/foundry-validator-copilot-home"
export COPILOT_AUTO_UPDATE=false
export GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS=false
mkdir -p "$COPILOT_HOME"
printf '%s\n' '{"disableAllHooks":true,"ide":{"autoConnect":false}}' \
  > "$COPILOT_HOME/settings.json"

if find "$workspace" \
  \( -path '*/.github/skills/*' \
     -o -path '*/.agents/skills/*' \
     -o -path '*/.claude/skills/*' \) \
  -type l -print -quit | grep -q .; then
  echo "::error::Project skill directories and files must not be symbolic links"
  exit 1
fi
while IFS= read -r skill_file; do
  if grep -Fq 'validate-foundry-ci' "$skill_file"; then
    echo "::error::The repository must not override the temporary validation skill"
    exit 1
  fi
done < <(
  find "$workspace" \
    \( -path '*/.github/skills/*/SKILL.md' \
       -o -path '*/.agents/skills/*/SKILL.md' \
       -o -path '*/.claude/skills/*/SKILL.md' \) \
    -type f -print
)

npm install --global --no-audit --no-fund @github/copilot@1.0.83
source_repo="$RUNNER_TEMP/github-copilot-for-azure-source"
skill_parent="$RUNNER_TEMP/foundry-validator-skills"
skill_root="$skill_parent/validate-foundry-ci"
git clone --depth 1 --filter=blob:none --sparse \
  --branch "$VALIDATION_REF" \
  https://github.com/microsoft/GitHub-Copilot-for-Azure.git "$source_repo"
git -C "$source_repo" sparse-checkout set \
  plugins/azure-skills/skills/microsoft-foundry/foundry-agent/validate
mkdir -p "$skill_root"
cp -R "$source_repo/plugins/azure-skills/skills/microsoft-foundry/foundry-agent/validate/." \
  "$skill_root/"
cp "$ACTION_PATH/skills/validate-foundry-ci/SKILL.md" "$skill_root/SKILL.md"
copilot skill add "$skill_parent"

resolved_skill="$(
  copilot skill list --json |
    jq -r '[.[] | select(.name == "validate-foundry-ci" and .enabled == true) | .path]
      | if length == 1 then .[0] else "" end'
)"
if [[ -z "$resolved_skill" || "$(realpath "$resolved_skill")" != "$(realpath "$skill_root")" ]]; then
  echo "::error::validate-foundry-ci must resolve to the downloaded temporary skill"
  exit 1
fi

if find "$validate_root" -type l -print -quit | grep -q .; then
  echo "::error::validate-path must not contain symbolic links"
  exit 1
fi

find "$validate_root" \( -type f -o -type d \) -printf '%m %p\0' > "$permissions_file"
permissions_locked=true
find "$validate_root" -type f -exec chmod a-w {} +
find "$validate_root" -type d -exec chmod a-w {} +

output_root="$RUNNER_TEMP/foundry-validation-output"
mkdir -p "$output_root"
prompt="Use the /validate-foundry-ci skill with validatePath=$validate_root and outputPath=$output_root.
Run the downloaded validation workflow once, process every discovered hosted
agent, write every report pair under outputPath, return its batch summary, then
print the exact CI_BATCH_OUTCOME line required by the CI wrapper."

batch_output="$RUNNER_TEMP/foundry-validation-batch.txt"
set +e
copilot -C "$validate_root" \
  --prompt "$prompt" \
  --add-dir "$skill_root" \
  --add-dir "$output_root" \
  --available-tools=view,grep,glob,edit,apply_patch,create \
  --allow-tool=write \
  --deny-tool=shell \
  --deny-tool=url \
  --disable-builtin-mcps \
  --no-ask-user \
  --no-auto-update \
  --no-custom-instructions \
  --silent | tee "$batch_output"
copilot_status="${PIPESTATUS[0]}"
set -e

reports_file="$RUNNER_TEMP/foundry-validation-reports.txt"
artifact_root="$RUNNER_TEMP/foundry-validation-artifacts"
mkdir -p "$artifact_root"
find "$output_root" -maxdepth 1 -type f \
  -name 'validation-*.md' -print0 |
  sort -z > "$reports_file"

report_count=0
overall_status="$copilot_status"
batch_outcome="$(
  sed -n 's/^CI_BATCH_OUTCOME=\(completed\|partial\|no-reports\|invalid-input\|no-hosted-agents\)$/\1/p' \
    "$batch_output" | tail -n 1
)"
if [[ "$batch_outcome" != "completed" ]]; then
  echo "::error::Foundry validation batch outcome: ${batch_outcome:-missing}"
  overall_status=1
fi

while IFS= read -r -d '' markdown_report; do
  json_report="${markdown_report%.md}.json"
  if [[ ! -f "$markdown_report" || -L "$markdown_report" || ! -s "$markdown_report" ]]; then
    overall_status=1
    continue
  fi

  report_count=$((report_count + 1))
  canonical_markdown="$(realpath "$markdown_report")"
  case "$canonical_markdown/" in
    "$output_root/"*) ;;
    *)
      echo "::error::Markdown report escaped outputPath"
      overall_status=1
      continue
      ;;
  esac
  report_stage="$artifact_root/report-$report_count"
  mkdir -p "$report_stage"
  cp "$canonical_markdown" "$report_stage/"

  json_valid=true
  if [[ ! -f "$json_report" || -L "$json_report" || ! -s "$json_report" ]]; then
    echo "::error::A Markdown report is missing its JSON companion"
    overall_status=1
    json_valid=false
  else
    canonical_json="$(realpath "$json_report")"
    case "$canonical_json/" in
      "$output_root/"*) cp "$canonical_json" "$report_stage/" ;;
      *)
        echo "::error::JSON report escaped outputPath"
        overall_status=1
        json_valid=false
        ;;
    esac
  fi

  if [[ "${GITHUB_EVENT_NAME:-}" == "pull_request" ]]; then
    comment_file="$RUNNER_TEMP/foundry-validation-comment-$report_count.md"
    report_name="$(basename "$markdown_report")"
    {
      echo "## Microsoft Foundry hosted-agent validation"
      echo
      echo "**Report:** \`$report_name\`"
      echo
      if [[ "$json_valid" != "true" ]]; then
        echo "**Warning:** The JSON companion report is missing or invalid."
        echo
      fi
      echo "[View workflow run]($GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID)"
      echo
      sed $'s/@/@\u200B/g' "$markdown_report"
    } > "$comment_file"
    if ! post_comment "$comment_file"; then
      echo "::error::Failed to post PR comment for $report_name"
      overall_status=1
    fi
  fi
done < "$reports_file"

while IFS= read -r -d '' json_report; do
  markdown_report="${json_report%.json}.md"
  if [[ ! -f "$markdown_report" || -L "$markdown_report" || ! -s "$markdown_report" ]]; then
    echo "::error::A JSON report is missing its Markdown companion"
    overall_status=1
  fi
done < <(
  find "$output_root" -maxdepth 1 -type f \
    -name 'validation-*.json' -print0 | sort -z
)

if [[ "$report_count" -eq 0 ]]; then
  echo "::error::No validation reports were produced"
  exit 1
fi
echo "Generated $report_count hosted-agent validation report(s)."
exit "$overall_status"
