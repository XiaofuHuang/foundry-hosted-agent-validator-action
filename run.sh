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
orchestrator_pid=""

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

cancel_run() {
  local exit_code="$1"
  trap - HUP INT TERM
  if [[ -n "$orchestrator_pid" ]] && kill -0 "$orchestrator_pid" 2>/dev/null; then
    kill -TERM "$orchestrator_pid" 2>/dev/null || true
    wait "$orchestrator_pid" 2>/dev/null || true
    orchestrator_pid=""
  fi
  exit "$exit_code"
}
trap 'cancel_run 129' HUP
trap 'cancel_run 130' INT
trap 'cancel_run 143' TERM

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

rules_file=""
rules_root=""
rules_kind=""
if [[ -n "${INPUT_RULES_FILE:-}" ]]; then
  rules_kind="explicit"
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
    echo "::error::rules-file must be relative to validate-path or use public HTTPS"
    exit 1
  else
    rules_candidate="$validate_root/$INPUT_RULES_FILE"
    rules_lexical="$(
      python3 - "$rules_candidate" <<'PY'
import os
import sys

print(os.path.abspath(sys.argv[1]))
PY
    )"
    case "$rules_lexical/" in
      "$validate_root/"*) ;;
      *)
        echo "::error::Local rules-file must stay inside validate-path"
        exit 1
        ;;
    esac
    if [[ -L "$rules_lexical" ]]; then
      echo "::error::Local rules-file must not be a symbolic link"
      exit 1
    fi
    rules_file="$(realpath -e "$rules_lexical")"
    case "$rules_file/" in
      "$validate_root/"*) ;;
      *)
        echo "::error::Local rules-file must resolve inside validate-path"
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
fi

copilot_template_home="$RUNNER_TEMP/foundry-validator-copilot-template-home"
export COPILOT_HOME="$copilot_template_home"
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
resolved_validation_commit="$(
  git -C "$source_repo" rev-parse --verify "HEAD^{commit}"
)"
echo "Resolved validation skill source commit: $resolved_validation_commit"
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

if [[ -z "$rules_file" ]]; then
  root_rules="$validate_root/.foundry/agent-validation-rules.yaml"
  if [[ -e "$root_rules" ]]; then
    if [[ ! -f "$root_rules" || ! -s "$root_rules" ]]; then
      echo "::error::Root agent-validation-rules.yaml must be a non-empty regular file"
      exit 1
    fi
    rules_file="$(realpath -e "$root_rules")"
    rules_kind="root"
  fi
fi

if [[ -n "$rules_file" ]]; then
  if [[ -z "$rules_root" ]]; then
    rules_root="$RUNNER_TEMP/foundry-validation-rules"
    mkdir -p "$rules_root"
    cp "$rules_file" "$rules_root/custom-rules.yaml"
    rules_file="$rules_root/custom-rules.yaml"
  fi
  find "$rules_root" -type f -exec chmod a-w {} +
  find "$rules_root" -type d -exec chmod a-w {} +
fi

invocations_file="$RUNNER_TEMP/foundry-validation-invocations.txt"
: > "$invocations_file"
previous_dir=""
while IFS= read -r -d '' azure_yaml; do
  invocation_path="${azure_yaml%/azure.yaml}"
  if [[ "$invocation_path" != "$previous_dir" ]]; then
    printf '%s\0' "$invocation_path" >> "$invocations_file"
    previous_dir="$invocation_path"
  fi
done < <(
  find "$validate_root" -type f -name 'azure.yaml' -print0 |
    LC_ALL=C sort -z
)

find "$validate_root" \( -type f -o -type d \) -printf '%m %p\0' > "$permissions_file"
permissions_locked=true
find "$validate_root" -type f -exec chmod a-w {} +
find "$validate_root" -type d -exec chmod a-w {} +

output_root="$RUNNER_TEMP/foundry-validation-output"
invocation_root="$RUNNER_TEMP/foundry-validation-invocations"
aggregate_state="$RUNNER_TEMP/foundry-validation-aggregate-state.json"
report_id="$(date -u +'%Y%m%dT%H%M%SZ')"
MAX_VALIDATION_WORKERS=3
mkdir -p "$output_root"
mkdir -p "$invocation_root"
echo "Validation report ID: $report_id"

find "$skill_parent" -type f -exec chmod a-w {} +
find "$skill_parent" -type d -exec chmod a-w {} +

orchestration_args=(
  python3 "$ACTION_PATH/scripts/run_invocations.py"
  --invocations-file "$invocations_file"
  --invocation-root "$invocation_root"
  --template-home "$copilot_template_home"
  --skill-root "$skill_root"
  --report-id "$report_id"
  --max-workers "$MAX_VALIDATION_WORKERS"
)
if [[ -n "$rules_root" ]]; then
  orchestration_args+=(
    --rules-root "$rules_root"
    --rules-file "$rules_file"
    --rules-kind "$rules_kind"
  )
fi

set +e
"${orchestration_args[@]}" &
orchestrator_pid="$!"
wait "$orchestrator_pid"
orchestration_status="$?"
orchestrator_pid=""
set -e

if [[ "$orchestration_status" -ne 0 ]]; then
  invocation_index=0
  while IFS= read -r -d '' invocation_path; do
    invocation_index=$((invocation_index + 1))
    invocation_worker="$invocation_root/$(printf '%06d' "$invocation_index")"
    copilot_status="not-started"
    if [[ -f "$invocation_worker/status" ]]; then
      read -r copilot_status < "$invocation_worker/status"
    fi
    invocation_status="copilot-failed"
    if [[ "$copilot_status" == "0" ]]; then
      invocation_status="success"
    fi
    if [[ "$invocation_path" == "$validate_root" ]]; then
      invocation_display="."
    else
      invocation_display="${invocation_path#"$validate_root"/}"
    fi
    echo "Validation invocation $invocation_index: path=$invocation_display status=$invocation_status copilotStatus=$copilot_status aggregationStatus=not-run reports=0 skillCommit=$resolved_validation_commit"
  done < "$invocations_file"
  echo "::error::At least one isolated Copilot validation invocation failed"
  exit 1
fi

overall_status=0
invocation_index=0
while IFS= read -r -d '' invocation_path; do
  invocation_index=$((invocation_index + 1))
  invocation_worker="$invocation_root/$(printf '%06d' "$invocation_index")"
  invocation_output="$invocation_worker/output"
  read -r copilot_status < "$invocation_worker/status"
  invocation_status="success"
  invocation_report_count=0
  aggregate_status="not-run"

  set +e
  if [[ "$invocation_path" == "$workspace" ]]; then
    invocation_agent_root="$workspace"
  else
    invocation_agent_root="${invocation_path#"$workspace"/}"
  fi
  invocation_report_count="$(
    python3 "$ACTION_PATH/scripts/aggregate_reports.py" \
      --source "$invocation_output" \
      --output "$output_root" \
      --state "$aggregate_state" \
      --report-id "$report_id" \
      --agent-root "$invocation_agent_root"
  )"
  aggregate_status="$?"
  set -e
  if [[ "$aggregate_status" -ne 0 ||
        ! "$invocation_report_count" =~ ^[0-9]+$ ]]; then
    invocation_status="aggregation-failed"
    invocation_report_count=0
    overall_status=1
  elif [[ "$invocation_report_count" -eq 0 ]]; then
    invocation_status="no-hosted-reports"
  fi

  if [[ "$invocation_path" == "$validate_root" ]]; then
    invocation_display="."
  else
    invocation_display="${invocation_path#"$validate_root"/}"
  fi
  echo "Validation invocation $invocation_index: path=$invocation_display status=$invocation_status copilotStatus=$copilot_status aggregationStatus=$aggregate_status reports=$invocation_report_count skillCommit=$resolved_validation_commit"
done < "$invocations_file"

reports_file="$RUNNER_TEMP/foundry-validation-reports.txt"
artifact_root="$RUNNER_TEMP/foundry-validation-artifacts"
mkdir -p "$artifact_root"
: > "$reports_file"
if [[ -f "$aggregate_state" ]]; then
  while IFS= read -r normalized_name; do
    printf '%s\0' \
      "$output_root/validation-$report_id-$normalized_name.md" \
      >> "$reports_file"
  done < <(jq -r '.usedNames[]' "$aggregate_state")
fi

report_count=0
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
    -name 'validation-*.json' -print0 | sort -z
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
    -name 'agent-validation-*-rules.yaml' -print0 | sort -z
)

if [[ "$report_count" -eq 0 ]]; then
  echo "::error::No validation reports were produced"
  exit 1
fi
echo "Generated $report_count hosted-agent validation report(s)."
exit "$overall_status"
