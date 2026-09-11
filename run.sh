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
agent_root="$(realpath -m "$workspace/${INPUT_AGENT_PATH:-.}")"
case "$agent_root/" in
  "$workspace/"*) ;;
  *)
    echo "::error::agent-path must stay inside GITHUB_WORKSPACE"
    exit 1
    ;;
esac
if [[ ! -d "$agent_root" ]]; then
  echo "::error::agent-path must be a directory"
  exit 1
fi

rules_file=""
rules_prompt=""
rules_root=""
if [[ -n "${INPUT_RULES_FILE:-}" ]]; then
  if [[ "$INPUT_RULES_FILE" == https://* ]]; then
    rules_root="$RUNNER_TEMP/foundry-validation-rules"
    rules_file="$rules_root/custom-rules.yaml"
    mkdir -p "$rules_root"
    read -r rules_host rules_port rules_ip < <(
      python3 - "$INPUT_RULES_FILE" <<'PY'
import ipaddress
import socket
import sys
import urllib.parse

value = sys.argv[1]
if not value.isascii():
    raise SystemExit("Remote rules-file URL must be ASCII")
parsed = urllib.parse.urlsplit(value)
if parsed.scheme != "https" or not parsed.hostname:
    raise SystemExit("Remote rules-file must use HTTPS")
if parsed.username is not None or parsed.password is not None:
    raise SystemExit("Remote rules-file URL must not contain credentials")
host = parsed.hostname
port = parsed.port or 443
addresses = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
public = sorted(
    {
        address[4][0]
        for address in addresses
        if ipaddress.ip_address(address[4][0]).is_global
    }
)
if not public:
    raise SystemExit("Remote rules-file host must resolve to a public IPv4 address")
print(host, port, public[0])
PY
    )
    http_status="$(
      curl --fail --silent --show-error \
      --proto '=https' --noproxy '*' \
      --resolve "$rules_host:$rules_port:$rules_ip" \
      --connect-timeout 10 --max-time 30 \
      --max-filesize 1048576 \
      "$INPUT_RULES_FILE" \
      --output "$rules_file" \
      --write-out '%{http_code}'
    )"
    if [[ "$http_status" != "200" ]]; then
      echo "::error::Remote rules-file must return HTTP 200 without redirects"
      exit 1
    fi
  elif [[ "$INPUT_RULES_FILE" == *://* || "$INPUT_RULES_FILE" == /* ]]; then
    echo "::error::rules-file must be relative to agent-path or use public HTTPS"
    exit 1
  else
    rules_candidate="$agent_root/$INPUT_RULES_FILE"
    rules_lexical="$(
      python3 - "$rules_candidate" <<'PY'
import os
import sys

print(os.path.abspath(sys.argv[1]))
PY
    )"
    case "$rules_lexical/" in
      "$agent_root/"*) ;;
      *)
        echo "::error::Local rules-file must stay inside agent-path"
        exit 1
        ;;
    esac
    if [[ -L "$rules_lexical" ]]; then
      echo "::error::Local rules-file must not be a symbolic link"
      exit 1
    fi
    rules_file="$(realpath -e "$rules_lexical")"
    case "$rules_file/" in
      "$agent_root/"*) ;;
      *)
        echo "::error::Local rules-file must resolve inside agent-path"
        exit 1
        ;;
    esac
    if [[ ! -f "$rules_file" || -L "$rules_file" ]]; then
      echo "::error::Local rules-file must be a regular non-symbolic-link file"
      exit 1
    fi
  fi
  if [[ ! -s "$rules_file" ]]; then
    echo "::error::rules-file must not be empty"
    exit 1
  fi
  rules_prompt=" Use rulesFile=$rules_file as the explicit caller rules file."
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

if find "$agent_root" -type l -print -quit | grep -q .; then
  echo "::error::agent-path must not contain symbolic links"
  exit 1
fi

find "$agent_root" \( -type f -o -type d \) -printf '%m %p\0' > "$permissions_file"
permissions_locked=true
find "$agent_root" -type f -exec chmod a-w {} +
find "$agent_root" -type d -exec chmod a-w {} +

output_root="$RUNNER_TEMP/foundry-validation-output"
mkdir -p "$output_root"
agent_key="$(printf '%s' "$agent_root" | sha256sum | cut -c1-8)"
report_id="github-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-${agent_key}"
artifact_name="foundry-validation-$report_id"
echo "artifact-name=$artifact_name" >> "$GITHUB_OUTPUT"
prompt="Use the /validate-foundry-ci skill with agentPath=$agent_root, outputPath=$output_root, and reportId=$report_id.
Run the downloaded validation workflow exactly once for this agent, write one
report pair under outputPath, and return the report paths.$rules_prompt"

set +e
copilot_args=(
  -C "$agent_root"
  --prompt "$prompt"
  --add-dir "$skill_root"
  --add-dir "$output_root"
)
if [[ -n "$rules_root" ]]; then
  copilot_args+=(--add-dir "$rules_root")
fi
copilot_args+=(
  --available-tools=view,grep,glob,edit,apply_patch,create
  --allow-tool=write
  --deny-tool=shell
  --deny-tool=url
  --disable-builtin-mcps
  --no-ask-user
  --no-auto-update
  --no-custom-instructions
  --silent
)
copilot "${copilot_args[@]}"
copilot_status="$?"
set -e

reports_file="$RUNNER_TEMP/foundry-validation-reports.txt"
artifact_root="$RUNNER_TEMP/foundry-validation-artifacts"
mkdir -p "$artifact_root"
find "$output_root" -maxdepth 1 -type f \
  -name "validation-$report_id*.md" -print0 |
  sort -z > "$reports_file"

expected_report_count="$(
  find "$output_root" -maxdepth 1 -type f \
    -name "validation-$report_id*.md" -printf '.' | wc -c | tr -d ' '
)"
if [[ "$expected_report_count" -ne 1 ]]; then
  echo "::error::Expected exactly one Markdown report, found $expected_report_count"
  exit 1
fi

report_count=0
overall_status="$copilot_status"
while IFS= read -r -d '' markdown_report; do
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

  if [[ "${GITHUB_EVENT_NAME:-}" == "pull_request" ]]; then
    report_name="$(basename "$markdown_report")"
    if ! post_comment "$markdown_report"; then
      echo "::error::Failed to post PR comment for $report_name"
      overall_status=1
    fi
  fi
done < "$reports_file"

while IFS= read -r -d '' json_report; do
  canonical_json="$(realpath "$json_report")"
  case "$canonical_json/" in
    "$output_root/"*)
      json_stage="$artifact_root/json-$(basename "$json_report" .json)"
      mkdir -p "$json_stage"
      cp "$canonical_json" "$json_stage/"
      ;;
  esac
done < <(
  find "$output_root" -maxdepth 1 -type f \
    -name "validation-$report_id*.json" -print0 | sort -z
)

while IFS= read -r -d '' merged_rules; do
  canonical_rules="$(realpath "$merged_rules")"
  case "$canonical_rules/" in
    "$output_root/"*)
      rules_stage="$artifact_root/rules"
      mkdir -p "$rules_stage"
      cp "$canonical_rules" "$rules_stage/"
      ;;
  esac
done < <(
  find "$output_root" -maxdepth 1 -type f \
    -name "agent-validation-$report_id-rules.yaml" -print0 | sort -z
)

echo "Generated one hosted-agent validation report."
exit "$overall_status"
