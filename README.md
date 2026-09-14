# Microsoft Foundry Hosted Agent Validator

A small composite GitHub Action that downloads the configured Microsoft Foundry
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
      - uses: XiaofuHuang/foundry-hosted-agent-validator-action@v6.0.1
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
`github-token`. Private cross-repository `github-rules` require a token that can
access the target repository. `rules-file` and `github-rules` cannot both be
set.

When supplied, the caller rules are merged over agent custom rules and default
rules. Matching rule IDs are replaced by the caller rule.

For multiple agents, call the Action once per agent with separate jobs or a
matrix. Each invocation still validates only one agent.

## How it works

1. **Prepare paths.** `scripts/prepare.mjs` keeps the agent inside the workspace,
   keeps local rules inside that agent, creates a unique report ID, and creates
   an isolated `COPILOT_HOME` in runner temporary storage.
2. **Optionally download GitHub rules.** `gh api` downloads `github-rules` into
   temporary storage after preparation enforces its mutual exclusion with
   `rules-file`.
3. **Install the validation skill.** `scripts/install-validation-skill.sh`
   sparsely downloads the configured `VALIDATION_REF`, overlays the single
   `validate-foundry-ci/SKILL.md` wrapper, and registers the combined skill.
4. **Run Copilot.** Pinned Copilot CLI `1.0.83` runs the downloaded workflow once
   for the selected agent and writes reports to runner temporary storage.
5. **Publish the PR result.** The Action requires exactly one non-empty Markdown
   report and posts it unchanged on pull requests. If no usable report exists,
   it posts a generic failure comment. An available report is still published
   when Copilot exits nonzero, and any validation failure leaves the Action
   unsuccessful.
6. **Upload the artifact.** Pinned `actions/upload-artifact` runs even after
   failure and uploads the generated report files for seven days.

The validation is an advisory review, not a compliance or security gate. JSON
reports are uploaded when present but are not checked or required.
