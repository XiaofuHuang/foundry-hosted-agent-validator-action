from __future__ import annotations

import hashlib
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTION = (ROOT / "action.yml").read_text(encoding="utf-8")


def test_action_metadata_contract() -> None:
    assert "name: Microsoft Foundry Hosted Agent Validator" in ACTION
    for input_name in (
        "github-token:",
        "agent-path:",
        "copilot-cli-version:",
        "comment-on-pr:",
    ):
        assert input_name in ACTION
    for output_name in ("json-report:", "markdown-report:", "conclusion:", "summary:"):
        assert output_name in ACTION
    assert re.search(r"github-token:\s*\n(?:.*\n){0,3}\s+required: true", ACTION)
    assert re.search(r"agent-path:\s*\n(?:.*\n){0,3}\s+default: \.", ACTION)
    assert re.search(r"comment-on-pr:\s*\n(?:.*\n){0,3}\s+default: \"true\"", ACTION)


def test_exact_tool_pin_and_isolation() -> None:
    assert "copilot-cli-version must be exactly 1.0.83" in ACTION
    assert 'COPILOT_AUTO_UPDATE: "false"' in ACTION
    assert '${{ github.action_path }}/runtime/package-lock.json' in ACTION
    assert "npm ci --ignore-scripts" in ACTION
    assert "npm install" not in ACTION
    assert "$runtime_install/node_modules/.bin/copilot" in ACTION
    assert '${{ github.action_path }}/scripts/validate-target.mjs' in ACTION
    assert "${{ github.action_path }}/skills" in ACTION
    assert "${{ github.action_path }}/scripts/" in ACTION
    assert "skill add" in ACTION
    assert '--add-dir "${{ github.action_path }}/skills/validate-foundry-ci"' in ACTION


def test_target_config_is_nested_evidence_not_copilot_configuration() -> None:
    assert '-C "$SHADOW_ROOT"' in ACTION
    assert '-C "$AGENT_ROOT"' not in ACTION
    add_dirs = re.findall(r"--add-dir[= ](?:\"([^\"]+)\"|(\S+))", ACTION)
    assert add_dirs == [('${{ github.action_path }}/skills/validate-foundry-ci', "")]
    assert '--add-dir "$EVIDENCE_ROOT"' not in ACTION
    assert '--add-dir "$AGENT_ROOT"' not in ACTION
    assert 'shadow_root="$RUNTIME_DIR/shadow"' in ACTION
    assert 'evidence_root="$shadow_root/evidence"' in ACTION
    assert '"disableAllHooks":true' in ACTION
    assert '"ide":{"autoConnect":false}' in ACTION
    assert '> "$copilot_home/settings.json"' in ACTION
    assert 'printf \'%s\\n\' \'{"lspServers":{}}\'' in ACTION
    assert 'GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS: "false"' in ACTION
    assert 'COPILOT_CUSTOM_INSTRUCTIONS_DIRS: ""' in ACTION
    assert "--no-custom-instructions" in ACTION
    assert "--disable-builtin-mcps" in ACTION
    assert "--plugin-dir" not in ACTION


def test_permissions_are_narrow() -> None:
    for forbidden in ("--yolo", "--allow-all", "--allow-all-tools", "--allow-all-paths"):
        assert forbidden not in ACTION
    assert "--available-tools=view,grep,glob,edit,apply_patch,create" in ACTION
    assert "--available-tools=read,grep,glob,write" not in ACTION
    assert '--allow-tool="write($JSON_REPORT)"' in ACTION
    assert '--allow-tool="write($MARKDOWN_REPORT)"' in ACTION
    assert "--deny-tool=shell" in ACTION
    assert "--deny-tool=url" in ACTION
    assert "--disable-builtin-mcps" in ACTION
    assert "--no-custom-instructions" in ACTION
    smoke = (ROOT / "scripts" / "smoke-cli.mjs").read_text(encoding="utf-8")
    assert "--available-tools=view,grep,glob,edit,apply_patch,create" in smoke
    assert "COPILOT_OFFLINE: \"true\"" in smoke


def test_action_does_not_execute_or_install_target() -> None:
    assert 'cd "$runtime_install"' in ACTION
    assert "NPM_CONFIG_IGNORE_SCRIPTS=true" in ACTION
    assert "--ignore-scripts" in ACTION
    assert not re.search(r"\b(pip|poetry|uv|yarn|pnpm|azd|az)\s+(install|run|deploy|up|login)\b", ACTION)
    assert "python3 \"${{ github.action_path }}/scripts/" in ACTION


def test_outputs_are_emitted_only_after_verified_publication() -> None:
    assert "value: ${{ steps.outputs.outputs.json-report }}" in ACTION
    assert "value: ${{ steps.outputs.outputs.markdown-report }}" in ACTION
    publish = ACTION.index("- name: Validate and publish reports")
    outputs = ACTION.index("- name: Emit final action outputs")
    cleanup = ACTION.index("- name: Restore shadow permissions")
    assert publish < outputs < cleanup
    output_block = ACTION[outputs:cleanup]
    assert "if: always()" in output_block
    assert "report_outputs.py" in output_block
    assert "PUBLISH_OUTCOME: ${{ steps.publish.outcome }}" in output_block
    assert "--publication-state" in ACTION[publish:outputs]
    assert "--validation-state" in ACTION[publish:outputs]


def test_pull_request_comment_is_owned_by_the_action() -> None:
    assert "github.event_name == 'pull_request'" in ACTION
    assert "inputs.comment-on-pr == 'true'" in ACTION
    assert '${{ github.event.pull_request.number }}' in ACTION
    assert '${{ github.api_url }}' in ACTION
    assert '${{ inputs.github-token }}' in ACTION
    assert '${{ github.action_path }}/scripts/comment_pr.py' in ACTION


def test_workflow_uses_only_immutable_external_refs_and_never_invokes_action() -> None:
    workflows = list((ROOT / ".github" / "workflows").glob("*.y*ml"))
    assert workflows
    for workflow in workflows:
        text = workflow.read_text(encoding="utf-8")
        for reference in re.findall(r"^\s*uses:\s*([^\s#]+)", text, flags=re.MULTILINE):
            owner_action, separator, revision = reference.rpartition("@")
            assert separator and owner_action
            assert re.fullmatch(r"[0-9a-f]{40}", revision)
        assert "copilot-requests" not in text
        assert "uses: ./" not in text
        assert "smoke-cli" not in text
        assert "node_modules/.bin/copilot" not in text


def test_bundled_sources_match_upstream_hashes() -> None:
    expected = {
        "validate.md": "e8d1955f79a21c46dd0f3085eb712df8d422530334bc45a2ae561cb09311dc73",
        "references/default-rules.yaml": "bba89ad3ce259acaf2734704657f788aadb6e5c2f2f887fd72be03bba4ede1af",
        "references/report-schema.json": "88b8952b9446d26c704e443e758b55d312d7645e68374fa0786cf85a92d5bbb4",
        "references/report-template.md": "10b7db0fa337303168ea18d67bcb3ffdb080be867c90eed4c2f57aebe0809b16",
        "references/rules-schema.json": "8e156c8a8fb38382461f2f17659eefcb18aa7ad78cdf9b1d4b5d20361919b474",
    }
    skill = ROOT / "skills" / "validate-foundry-ci"
    for relative, digest in expected.items():
        content = (skill / relative).read_bytes()
        assert b"\r\n" not in content
        assert hashlib.sha256(content).hexdigest() == digest
