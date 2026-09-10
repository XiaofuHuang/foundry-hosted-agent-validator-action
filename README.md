# Microsoft Foundry Hosted Agent Validator

A small composite GitHub Action that downloads configured Microsoft Foundry
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
      - uses: XiaofuHuang/foundry-hosted-agent-validator-action@v4.5.1
        with:
          github-token: ${{ github.token }}
          rules-file: foundry/agent-validation-rules.yaml
```

The Action finds every `azure.yaml` below `validate-path` in lexical order and
runs the validation workflow serially for each unique containing directory.
Each bounded invocation processes only the `azure.yaml` directly in that
directory; nested files receive their own invocation.
Every invocation receives one Action-owned UTC report ID and writes to an
isolated temporary output directory. The Action then aggregates each complete
JSON/Markdown pair into a shared `outputPath` in discovery order, globally
normalizes service names, and rewrites each JSON `markdownPath` to its final
path. It also normalizes every JSON and Markdown Agent root to the original
invocation directory relative to `GITHUB_WORKSPACE` (or the original absolute
workspace path when the invocation is the workspace root), preserving the rest
of the Markdown unchanged.

The Action fails when a Copilot invocation fails, a report pair is incomplete
or malformed for aggregation, merged-rules artifacts differ, or no reports are
produced overall. It uploads all final JSON/Markdown pairs and one byte-identical
`agent-validation-<reportId>-rules.yaml`; each Markdown report is posted as a
new PR comment.

## Inputs

| Input | Default | Purpose |
|---|---|---|
| `github-token` | Required | Runs Copilot and posts PR comments |
| `validate-path` | `.` | Passed to the validation workflow as `workspacePath` |
| `rules-file` | Empty | Local path relative to `validate-path`, or public HTTPS URL returning raw YAML |

Examples:

```yaml
# Local rules committed in the repository
rules-file: foundry/agent-validation-rules.yaml

# Public remote rules; no authentication header is sent
rules-file: https://raw.githubusercontent.com/owner/repo/main/rules.yaml
```

Local rules must resolve inside `validate-path` and cannot be symbolic links.
Remote rules must be public raw content over HTTPS, resolve to a public IPv4
address, return HTTP 200 without redirects, and are limited to 1 MiB and a
30-second transfer.

When supplied, `rules-file` is passed to every bounded invocation and remains
the highest-precedence rule source. Otherwise,
`validate-path/.foundry/agent-validation-rules.yaml`, when present, is passed
to every invocation as the root caller rules. Local caller rules are copied
into Action-controlled temporary storage before the target tree is made
read-only. Defaults still come from the downloaded skill. Matching rule IDs are
replaced by the caller rule.

## Scope

The Action pins Copilot CLI `1.0.83`, then sparsely downloads only
`plugins/azure-skills/skills/microsoft-foundry/foundry-agent/validate/` from
the configured `VALIDATION_REF`. This repository contains only a small
`validate-foundry-ci/SKILL.md` CI wrapper; it does not duplicate Microsoft's
`validate.md`, rules, schemas, or report template. The Action combines them in
runner temporary storage, and `azd` is not installed.

It asks Copilot to inspect files statically and prohibits target execution,
target dependency installation, Canvas, shell access, and Azure access.

The trusted Action orchestration parses generated JSON only to verify report
pairing and aggregation metadata. It does not schema-validate results or
independently verify rule completeness; the experimental E2E verifier measures
those skill outcomes. This remains an advisory review, not a compliance or
security gate.

The runtime source is controlled by the single `VALIDATION_REF` value in
`action.yml`. This experimental branch uses
`fix/validation-batch-completeness`, and `run.sh` logs the exact resolved source
commit so E2E runs can be reproduced. Change `VALIDATION_REF` to `main` when the
validation update is published.
