import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { collect } from "../scripts/collect-report.mjs";

async function fixture(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "foundry-collect-"));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const outputRoot = path.join(root, "output");
  await fs.mkdir(outputRoot, { recursive: true });
  return {
    root,
    outputRoot,
    outputFile: path.join(root, "outputs.txt"),
  };
}

function environment(paths) {
  return {
    GITHUB_OUTPUT: paths.outputFile,
    OUTPUT_ROOT: paths.outputRoot,
    REPORT_ID: "github-1234-1-abcd1234",
  };
}

test("accepts one Markdown report alongside optional files", async (t) => {
  const paths = await fixture(t);
  const id = "github-1234-1-abcd1234";
  const markdownName = `validation-${id}.md`;
  const jsonName = `validation-${id}.json`;
  const rulesName = `agent-validation-${id}-rules.yaml`;
  await fs.writeFile(path.join(paths.outputRoot, markdownName), "# Validation\n");
  await fs.writeFile(path.join(paths.outputRoot, jsonName), "{}\n");
  await fs.writeFile(path.join(paths.outputRoot, rulesName), "rules: []\n");

  const result = await collect(environment(paths));

  assert.equal(result.reportPath, path.join(paths.outputRoot, markdownName));
  assert.equal(await fs.readFile(path.join(paths.outputRoot, jsonName), "utf8"), "{}\n");
  assert.equal(
    await fs.readFile(path.join(paths.outputRoot, rulesName), "utf8"),
    "rules: []\n",
  );
  assert.match(
    await fs.readFile(paths.outputFile, "utf8"),
    new RegExp(`report-path=.*${markdownName.replaceAll(".", "\\.")}`),
  );
});

test("reports zero Markdown files through step outputs", async (t) => {
  const paths = await fixture(t);

  await assert.rejects(
    collect(environment(paths)),
    /Expected exactly one Markdown report, found 0/,
  );

  assert.equal(
    await fs.readFile(paths.outputFile, "utf8"),
    "report-count=0\nreport-path=\n",
  );
});

test("rejects an empty Markdown report", async (t) => {
  const paths = await fixture(t);
  const id = "github-1234-1-abcd1234";
  await fs.writeFile(path.join(paths.outputRoot, `validation-${id}.md`), "");

  await assert.rejects(
    collect(environment(paths)),
    /generated Markdown report is empty/,
  );
});
