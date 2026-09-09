# Microsoft Foundry Hosted Agent Validator

A small composite GitHub Action that runs the Microsoft Foundry validation
skill and posts the Markdown report on pull requests.

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

Each pull-request run creates a new report comment. The Action also writes JSON
and Markdown reports under `.foundry/results/`.

## Inputs

| Input | Default | Purpose |
|---|---|---|
| `github-token` | Required | Runs Copilot and posts PR comments |
| `agent-path` | `.` | Directory containing `azure.yaml` |
| `comment-on-pr` | `true` | Posts a report for `pull_request` events |

## Scope

The Action pins Copilot CLI `1.0.83` and bundles the reviewed validation skill.
It asks Copilot to inspect files statically and prohibits target execution,
target dependency installation, Canvas, shell access, and Azure access.

This simplified version checks only that both report files exist. It does not
schema-validate report contents and is intended as an advisory review, not a
compliance or security gate.

See [UPSTREAM.md](UPSTREAM.md) for skill provenance.
