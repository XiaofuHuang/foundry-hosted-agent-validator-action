import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  isPublicIPv4,
  parseRemoteRulesUrl,
  prepare,
} from "../scripts/prepare.mjs";

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
    GITHUB_ACTION_PATH: paths.root,
    GITHUB_OUTPUT: paths.outputFile,
    GITHUB_RUN_ATTEMPT: "2",
    GITHUB_RUN_ID: "1234",
    GITHUB_WORKSPACE: paths.workspace,
    INPUT_AGENT_PATH: "agent",
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
  assert.ok(outputs["copilot-home"].startsWith(path.join(paths.root, "runner")));
  assert.match(
    await fs.readFile(outputs["prompt-file"], "utf8"),
    /rulesFile=.*rules\.yaml/,
  );
  assert.match(
    await fs.readFile(paths.outputFile, "utf8"),
    /artifact-name=foundry-validation-github-1234-2-/,
  );
});

test("downloads HTTPS caller rules into the isolated runtime", async (t) => {
  const paths = await fixture(t);
  let requestedUrl;

  const outputs = await prepare(
    environment(paths, {
      INPUT_RULES_FILE: "https://example.com/rules.yaml",
    }),
    {
      async downloadRemoteRules(url, destination) {
        requestedUrl = url;
        await fs.mkdir(path.dirname(destination), { recursive: true });
        await fs.writeFile(destination, "rules: []\n");
      },
    },
  );

  assert.equal(requestedUrl, "https://example.com/rules.yaml");
  assert.equal(await fs.readFile(outputs["rules-file"], "utf8"), "rules: []\n");
  assert.equal(path.dirname(outputs["rules-file"]), outputs["rules-root"]);
});

test("rejects agent paths outside the workspace", async (t) => {
  const paths = await fixture(t);
  await fs.mkdir(path.join(paths.root, "outside"), { recursive: true });

  await assert.rejects(
    prepare(environment(paths, { INPUT_AGENT_PATH: "../outside" })),
    /agent-path must stay inside GITHUB_WORKSPACE/,
  );
});

test("classifies public and non-public IPv4 addresses", () => {
  assert.equal(isPublicIPv4("8.8.8.8"), true);
  assert.equal(isPublicIPv4("10.0.0.1"), false);
  assert.equal(isPublicIPv4("127.0.0.1"), false);
  assert.equal(isPublicIPv4("169.254.169.254"), false);
  assert.equal(isPublicIPv4("203.0.113.1"), false);
  assert.equal(isPublicIPv4("::1"), false);
});

test("requires HTTPS URLs without embedded credentials", () => {
  assert.equal(
    parseRemoteRulesUrl("https://example.com/rules.yaml").hostname,
    "example.com",
  );
  assert.throws(
    () => parseRemoteRulesUrl("http://example.com/rules.yaml"),
    /must use HTTPS/,
  );
  assert.throws(
    () => parseRemoteRulesUrl("https://user:secret@example.com/rules.yaml"),
    /must not contain credentials/,
  );
});
