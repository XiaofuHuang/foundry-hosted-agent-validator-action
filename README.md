# Microsoft Foundry Hosted Agent Validator

A reusable composite GitHub Action for static, repository-only validation of one Microsoft Foundry hosted agent. It uses a reviewed, bundled ruleset and fails closed when its reports are missing, malformed, incomplete, or altered.

The action supports `ubuntu-latest` runners. It does not run target code, install target dependencies, authenticate to Azure, query deployed resources, deploy, or invoke the agent.

## Usage

Pin this public action to the full 40-character commit SHA that you reviewed. Do not use a branch or moving tag.

```yaml
name: Validate hosted agent

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read
  pull-requests: write
  copilot-requests: write

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803
      - id: foundry-validation
        uses: XiaofuHuang/foundry-hosted-agent-validator-action@<full-40-character-commit-sha>
        with:
          github-token: ${{ github.token }}
          agent-path: .
      - if: always() && steps.foundry-validation.outputs.json-report != ''
        run: |
          echo "JSON: ${{ steps.foundry-validation.outputs.json-report }}"
          echo "Markdown: ${{ steps.foundry-validation.outputs.markdown-report }}"
```

For pull-request comments, the caller grants `contents: read`, `pull-requests: write`, and `copilot-requests: write`. Repository or organization Copilot policy must also permit GitHub Copilot CLI requests.

> **Fork warning:** GitHub does not pass a writable token to untrusted fork pull requests. Do not work around this with `pull_request_target` while checking out or validating fork-controlled code. Run the action only on trusted code, or use a separately reviewed workflow that never combines privileged tokens with an untrusted checkout.

## Inputs

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `github-token` | Yes | — | Caller token with `copilot-requests: write`. |
| `agent-path` | No | `.` | Hosted-agent root inside `GITHUB_WORKSPACE`. |
| `copilot-cli-version` | No | `1.0.83` | Compatibility input. Any value other than the reviewed pin `1.0.83` is rejected. |
| `comment-on-pr` | No | `true` | Create or update one validation report comment when invoked by a `pull_request` workflow. |

The selected path must contain `azure.yaml`, and static parsing must find exactly one service whose `host` is `azure.ai.agent`.

## Outputs

| Output | Description |
| --- | --- |
| `json-report` | Absolute path to the schema- and semantics-validated JSON report. |
| `markdown-report` | Absolute path to the validated Markdown companion report. |
| `conclusion` | `success` or `failure`. |
| `summary` | Concise pass/fail/inconclusive/skipped counts. |

Reports are the only target writes and are stored under:

```text
<agent-path>/.foundry/results/validation-<UTC-report-id>.json
<agent-path>/.foundry/results/validation-<UTC-report-id>.md
```

A failed `error` or `warning` rule fails the action. Recommendation failures and all `inconclusive` results are advisory. Missing or invalid reports always fail the action.

When invoked by a `pull_request` event with `comment-on-pr: true`, the Action
creates a PR comment containing the report and workflow run link. Each run
creates a new comment. It posts a fail-closed message when no report can be
published.

## Security model

- The action uses `npm ci` with an action-owned lockfile that pins `@github/copilot@1.0.83`, every platform package, `detect-libc`, and the data-only `yaml` parser by exact version and integrity. The local runtime, home, and `COPILOT_HOME` are isolated under runner temporary storage and removed afterward.
- Before Copilot starts, the action rejects every symlink and non-regular entry in the target, then copies the complete target into a nested `evidence` directory under a clean, non-Git shadow root. Copilot runs from the shadow root, never from the target and never with the target passed to `--add-dir`.
- The bundled CI skill always uses the bundled default rules. Target custom rules, repository instructions, prompts, and skills are treated as untrusted evidence and ignored as instructions.
- Target `.github` content remains available beneath `evidence` for review but is not discoverable as root configuration. Isolated settings disable all hooks and IDE auto-connect; root instructions, LSP, MCP, plugins, agents, skills, hooks, and extensions cannot auto-load from the target.
- Copilot receives only the CLI 1.0.83 file-inspection and patch-creation tools, with write approval scoped to the two shadow report paths. Shell, URL, MCP, remote control, broad tool permissions, and custom repository instructions are disabled. The shadow evidence is read-only except `.foundry/results`.
- Static target selection parses `azure.yaml` with the locked `yaml` package using the core data schema and inspects only direct `services.<name>.host` values. It never imports target Python or evaluates target configuration.
- The JSON report is schema- and semantics-validated in the shadow. Markdown is then rendered deterministically from that validated JSON, replacing any model-authored Markdown. Only the validated pair is copied to the original `<agent-path>/.foundry/results`; malformed output is never published.
- Successful report paths are emitted only after both destination files exist and match their validated shadow sources. The final always-running output step emits empty paths and `failure` when setup, validation, or publication did not complete.
- A trusted postprocessor validates the complete JSON shape and requires exactly one ordered result per bundled rule. `ruleId`, `title`, `level`, and `guidance` must match the trusted rules exactly.
- The Markdown report and GitHub job summary can contain model-generated conclusions. Treat report text as review evidence, not as certification or a deployed-environment assessment.

The model necessarily receives repository content needed for the review through GitHub Copilot. Do not use the action on content that your Copilot data policy does not permit.

## Development

Publisher CI runs contract and helper tests only; it never invokes Copilot.

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

See [UPSTREAM.md](UPSTREAM.md) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for bundled source provenance.
