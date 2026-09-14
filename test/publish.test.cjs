const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const publish = require("../scripts/publish.cjs");

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "foundry-publish-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const outputRoot = path.join(root, "output");
  const artifactRoot = path.join(root, "artifacts");
  fs.mkdirSync(outputRoot, { recursive: true });
  return { root, outputRoot, artifactRoot };
}

function harness() {
  const comments = [];
  const failures = [];
  const outputs = {};
  return {
    comments,
    failures,
    outputs,
    github: {
      rest: {
        issues: {
          async createComment(input) {
            comments.push(input);
          },
        },
      },
    },
    context: {
      eventName: "pull_request",
      payload: { pull_request: { number: 7 } },
      repo: { owner: "owner", repo: "repository" },
    },
    core: {
      info() {},
      setFailed(message) {
        failures.push(message);
      },
      setOutput(name, value) {
        outputs[name] = value;
      },
    },
  };
}

function environment(paths, overrides = {}) {
  return {
    ARTIFACT_ROOT: paths.artifactRoot,
    GITHUB_REPOSITORY: "owner/repository",
    GITHUB_RUN_ID: "1234",
    GITHUB_SERVER_URL: "https://github.com",
    INSTALL_OUTCOME: "success",
    OUTPUT_ROOT: paths.outputRoot,
    PREPARE_OUTCOME: "success",
    REPORT_ID: "github-1234-1-abcd1234",
    RUNNER_TEMP: paths.root,
    SETUP_OUTCOME: "success",
    SKILL_OUTCOME: "success",
    VALIDATION_OUTCOME: "success",
    ...overrides,
  };
}

test("stages reports and posts Markdown unchanged", async (t) => {
  const paths = fixture(t);
  const id = "github-1234-1-abcd1234";
  const markdown = `# Validation\n\nResult for ${id}\n`;
  fs.writeFileSync(path.join(paths.outputRoot, `validation-${id}.md`), markdown);
  fs.writeFileSync(path.join(paths.outputRoot, `validation-${id}.json`), "{}\n");
  fs.writeFileSync(
    path.join(paths.outputRoot, `agent-validation-${id}-rules.yaml`),
    "rules: []\n",
  );
  const tools = harness();

  await publish(tools, environment(paths));

  assert.deepEqual(tools.failures, []);
  assert.equal(tools.comments.length, 1);
  assert.equal(tools.comments[0].body, markdown);
  assert.equal(tools.comments[0].issue_number, 7);
  assert.equal(
    fs.readFileSync(
      path.join(paths.artifactRoot, "report-1", `validation-${id}.md`),
      "utf8",
    ),
    markdown,
  );
  assert.ok(
    fs.existsSync(
      path.join(paths.artifactRoot, `json-validation-${id}`, `validation-${id}.json`),
    ),
  );
  assert.ok(
    fs.existsSync(
      path.join(
        paths.artifactRoot,
        "rules",
        `agent-validation-${id}-rules.yaml`,
      ),
    ),
  );
});

test("posts a failure comment when no report exists", async (t) => {
  const paths = fixture(t);
  const tools = harness();

  await publish(
    tools,
    environment(paths, { VALIDATION_OUTCOME: "failure" }),
  );

  assert.equal(tools.comments.length, 1);
  assert.match(tools.comments[0].body, /Validation failed before any report/);
  assert.equal(tools.failures.length, 1);
  assert.match(tools.failures[0], /Expected exactly one Markdown report, found 0/);
  assert.match(tools.failures[0], /Validate hosted agent finished with outcome failure/);
});

test("keeps the report comment but fails when Copilot exits nonzero", async (t) => {
  const paths = fixture(t);
  const id = "github-1234-1-abcd1234";
  const markdown = "# Validation\n\nPartial report\n";
  fs.writeFileSync(path.join(paths.outputRoot, `validation-${id}.md`), markdown);
  const tools = harness();

  await publish(
    tools,
    environment(paths, { VALIDATION_OUTCOME: "failure" }),
  );

  assert.equal(tools.comments[0].body, markdown);
  assert.equal(tools.failures.length, 1);
  assert.match(tools.failures[0], /Validate hosted agent finished with outcome failure/);
});
