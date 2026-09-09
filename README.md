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
    paths: [azure.yaml, "src/**"]

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
      - uses: XiaofuHuang/foundry-hosted-agent-validator-action@<full-commit-sha>
        with:
          github-token: ${{ github.token }}
```

The validation workflow recursively finds every `azure.yaml` below
`validate-path` and validates each `azure.ai.agent` service separately.
Reports use its shared `outputPath`, the Action uploads all JSON/Markdown
pairs, and each Markdown report creates a new PR comment.

## Inputs

| Input | Default | Purpose |
|---|---|---|
| `github-token` | Required | Runs Copilot and posts PR comments |
| `validate-path` | `.` | Directory recursively searched for hosted agents |

## Scope

The Action pins Copilot CLI `1.0.83`, then sparsely downloads only
`plugins/azure-skills/skills/microsoft-foundry/foundry-agent/validate/` from
the configured `VALIDATION_REF`. This repository contains only a small
`validate-foundry-ci/SKILL.md` CI wrapper; it does not duplicate Microsoft's
`validate.md`, rules, schemas, or report template. The Action combines them in
runner temporary storage, and `azd` is not installed.

It asks Copilot to inspect files statically and prohibits target execution,
target dependency installation, Canvas, shell access, and Azure access.

This simplified version checks only that both report files exist. It does not
schema-validate report contents and is intended as an advisory review, not a
compliance or security gate.

The runtime source is controlled by the single `VALIDATION_REF` value in
`action.yml`. Change that value to `main` when the validation update is
published.
