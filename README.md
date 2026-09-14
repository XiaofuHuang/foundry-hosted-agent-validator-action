# Microsoft Foundry Hosted Agent Validator

A small composite GitHub Action that downloads the latest Microsoft Foundry
hosted-agent validation files at runtime and posts the Markdown report on pull
requests.

## Usage

Add this workflow to the default branch:

```yaml
name: Validate Foundry agent

on:
  pull_request:
    paths: ["agents/my-agent/**"]

permissions:
  contents: read
  pull-requests: write
  copilot-requests: write

jobs:
  validate:
    if: github.event.pull_request.head.repo.full_name == github.repository
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803
        with:
          ref: ${{ github.event.pull_request.head.sha }}
          persist-credentials: false
      - uses: XiaofuHuang/foundry-hosted-agent-validator-action@v6.0.0
        with:
          github-token: ${{ github.token }}
          agent-path: agents/my-agent
          rules-file: foundry/agent-validation-rules.yaml
```

Each Action invocation validates exactly one hosted agent. It generates one
Markdown report, uploads its report files and merged rules, and posts the
generated Markdown unchanged as one PR comment.

The Action uses the Copilot process result and generated Markdown reports as
its status. JSON reports are uploaded when present but are not checked or
required. Exactly one Markdown report is required per invocation.

## Inputs

| Input | Default | Purpose |
|---|---|---|
| `github-token` | Required | Runs Copilot and posts PR comments |
| `agent-path` | `.` | Passed to the validation workflow as `agentPath` |
| `rules-file` | Empty | Local path relative to `agent-path` |
| `github-rules` | Empty | GitHub file as `owner/repository/path@ref` |

Examples:

```yaml
# Local rules committed in the repository
rules-file: foundry/agent-validation-rules.yaml

# Rules stored in a GitHub repository
github-rules: owner/repo/rules.yaml@main
```

Local rules must resolve to a regular file inside `agent-path`. GitHub rules
are downloaded through the GitHub Contents API with `gh api` and
`github-token`. `rules-file` and `github-rules` cannot both be set.

When supplied, the caller rules are merged over agent custom rules and default
rules. Matching rule IDs are replaced by the caller rule.

For multiple agents, call the Action once per agent with separate jobs or a
matrix. Each invocation still validates only one agent.

## Scope

The composite Action exposes each lifecycle phase as a named GitHub Actions
step. It uses `actions/setup-node` for the runtime, `actions/github-script` for
PR comments, and `actions/upload-artifact` for reports. Foundry-specific logic
is split between small preparation and skill-download helpers plus declarative
GitHub Actions steps instead of one orchestration script.

The Action runs Copilot with an isolated `COPILOT_HOME`, pins Copilot CLI
`1.0.83`, then sparsely downloads only
`plugins/azure-skills/skills/microsoft-foundry/foundry-agent/validate/` from
the configured `VALIDATION_REF`. This repository contains only a small
`validate-foundry-ci/SKILL.md` CI wrapper; it does not duplicate Microsoft's
`validate.md`, rules, schemas, or report template. The Action combines them in
runner temporary storage, and `azd` is not installed.

It asks Copilot to run the downloaded validation workflow once for the selected
agent and write the report to runner temporary storage.

This simplified version checks only that exactly one Markdown report exists.
It does not schema-validate report contents and is intended as an advisory
review, not a compliance or security gate.

The implementation is organized as:

- `scripts/prepare.mjs`: inputs, paths, optional caller rules, and report IDs
- `scripts/fetch-skill.sh`: sparse skill download and registration
- `action.yml`: Copilot execution, report validation, PR comments, and artifacts

The runtime source is controlled by the single `VALIDATION_REF` value in
`action.yml`. Change that value to `main` when the validation update is
published.
