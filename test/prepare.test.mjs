import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { parseGitHubRules, prepare } from "../scripts/prepare.mjs";

async function fixture(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "foundry-prepare-"));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const workspace = path.join(root, "workspace");
  const agent = path.join(workspace, "agent");
  await fs.mkdir(agent, { recursive: true });
  return {
    root,
    workspace,
    agent,
    outputFile: path.join(root, "outputs.txt"),
  };
}

function environment(paths, overrides = {}) {
  return {
    GITHUB_OUTPUT: paths.outputFile,
    GITHUB_RUN_ATTEMPT: "2",
    GITHUB_RUN_ID: "1234",
    GITHUB_WORKSPACE: paths.workspace,
    INPUT_AGENT_PATH: "agent",
    INPUT_GITHUB_RULES: "",
    INPUT_GITHUB_TOKEN: "test-token",
    INPUT_RULES_FILE: "",
    RUNNER_TEMP: path.join(paths.root, "runner"),
    ...overrides,
  };
}

test("prepares an isolated single-agent validation", async (t) => {
  const paths = await fixture(t);
  const rulesFile = path.join(paths.agent, "rules.yaml");
  await fs.writeFile(rulesFile, "rules: []\n");

  const outputs = await prepare(
    environment(paths, { INPUT_RULES_FILE: "rules.yaml" }),
  );

  assert.equal(outputs["agent-root"], await fs.realpath(paths.agent));
  assert.equal(outputs["rules-file"], await fs.realpath(rulesFile));
  assert.match(outputs["report-id"], /^github-1234-2-[0-9a-f]{8}$/);
  assert.deepEqual(Object.keys(outputs).sort(), [
    "agent-root",
    "output-root",
    "report-id",
    "rules-endpoint",
    "rules-file",
    "runtime-root",
  ]);
  assert.equal(
    (await fs.stat(path.join(outputs["runtime-root"], "copilot-home"))).isDirectory(),
    true,
  );
});

test("prepares a GitHub API download for remote rules", async (t) => {
  const paths = await fixture(t);

  const outputs = await prepare(
    environment(paths, {
      INPUT_GITHUB_RULES: "owner/rules/path/rules.yaml@main",
    }),
  );

  assert.equal(
    outputs["rules-endpoint"],
    "repos/owner/rules/contents/path/rules.yaml?ref=main",
  );
  assert.equal(
    outputs["rules-file"],
    path.join(outputs["runtime-root"], "rules", "custom-rules.yaml"),
  );
});

test("rejects agent paths outside the workspace", async (t) => {
  const paths = await fixture(t);
  await fs.mkdir(path.join(paths.root, "outside"), { recursive: true });

  await assert.rejects(
    prepare(environment(paths, { INPUT_AGENT_PATH: "../outside" })),
    /agent-path must stay inside GITHUB_WORKSPACE/,
  );
});

test("parses GitHub rules references", () => {
  assert.equal(
    parseGitHubRules("owner/repo/rules/file.yaml@feature/rules"),
    "repos/owner/repo/contents/rules/file.yaml?ref=feature%2Frules",
  );
  assert.throws(
    () => parseGitHubRules("owner/repo/rules.yaml"),
    /must use owner\/repository\/path@ref/,
  );
});

test("rejects local and GitHub rules together", async (t) => {
  const paths = await fixture(t);
  await fs.writeFile(path.join(paths.agent, "rules.yaml"), "rules: []\n");

  await assert.rejects(
    prepare(
      environment(paths, {
        INPUT_GITHUB_RULES: "owner/repo/rules.yaml@main",
        INPUT_RULES_FILE: "rules.yaml",
      }),
    ),
    /cannot both be set/,
  );
});
