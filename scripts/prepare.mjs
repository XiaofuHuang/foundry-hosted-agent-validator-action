import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

function requireValue(env, name) {
  const value = env[name];
  if (!value) {
    throw new Error(`${name} must not be empty`);
  }
  return value;
}

function isInside(root, candidate) {
  const relative = path.relative(root, candidate);
  return (
    relative === "" ||
    (!relative.startsWith(`..${path.sep}`) &&
      relative !== ".." &&
      !path.isAbsolute(relative))
  );
}

async function resolveAgentRoot(workspace, inputPath) {
  let agentRoot;
  try {
    agentRoot = await fs.realpath(path.resolve(workspace, inputPath || "."));
  } catch {
    throw new Error("agent-path must be a directory");
  }
  if (!isInside(workspace, agentRoot)) {
    throw new Error("agent-path must stay inside GITHUB_WORKSPACE");
  }
  if (!(await fs.stat(agentRoot)).isDirectory()) {
    throw new Error("agent-path must be a directory");
  }
  return agentRoot;
}

async function resolveLocalRules(agentRoot, inputPath) {
  let rulesFile;
  try {
    rulesFile = await fs.realpath(path.resolve(agentRoot, inputPath));
  } catch {
    throw new Error("Local rules-file must be a regular file");
  }
  if (!isInside(agentRoot, rulesFile)) {
    throw new Error("Local rules-file must resolve inside agent-path");
  }
  if (!(await fs.stat(rulesFile)).isFile()) {
    throw new Error("Local rules-file must be a regular file");
  }
  return rulesFile;
}

function appendOutputs(outputFile, outputs) {
  const content = Object.entries(outputs)
    .map(([name, value]) => `${name}=${value}`)
    .join("\n");
  return fs.appendFile(outputFile, `${content}\n`);
}

export function parseGitHubRules(value) {
  const separator = value.lastIndexOf("@");
  if (separator <= 0 || separator === value.length - 1) {
    throw new Error("github-rules must use owner/repository/path@ref");
  }
  const source = value.slice(0, separator);
  const ref = value.slice(separator + 1);
  const parts = source.split("/");
  if (parts.length < 3 || parts.some((part) => !part) || !ref) {
    throw new Error("github-rules must use owner/repository/path@ref");
  }
  const [owner, repository, ...fileParts] = parts;
  const filePath = fileParts.map(encodeURIComponent).join("/");
  return `repos/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/contents/${filePath}?ref=${encodeURIComponent(ref)}`;
}

export async function prepare(env = process.env) {
  requireValue(env, "INPUT_GITHUB_TOKEN");
  const workspace = await fs.realpath(requireValue(env, "GITHUB_WORKSPACE"));
  const runnerTemp = path.resolve(requireValue(env, "RUNNER_TEMP"));
  const runId = requireValue(env, "GITHUB_RUN_ID");
  const runAttempt = requireValue(env, "GITHUB_RUN_ATTEMPT");
  const outputFile = requireValue(env, "GITHUB_OUTPUT");
  const agentRoot = await resolveAgentRoot(workspace, env.INPUT_AGENT_PATH || ".");
  const agentKey = createHash("sha256").update(agentRoot).digest("hex").slice(0, 8);
  const reportId = `github-${runId}-${runAttempt}-${agentKey}`;
  const runtimeRoot = path.join(runnerTemp, "foundry-validator", reportId);
  const outputRoot = path.join(
    runnerTemp,
    "foundry-validation-artifacts",
    reportId,
  );
  const copilotHome = path.join(runtimeRoot, "copilot-home");
  await Promise.all([
    fs.mkdir(outputRoot, { recursive: true }),
    fs.mkdir(copilotHome, { recursive: true }),
  ]);

  let rulesFile = "";
  let rulesEndpoint = "";
  const localRules = env.INPUT_RULES_FILE || "";
  const githubRules = env.INPUT_GITHUB_RULES || "";
  if (localRules && githubRules) {
    throw new Error("rules-file and github-rules cannot both be set");
  }
  if (localRules) {
    if (localRules.includes("://") || path.isAbsolute(localRules)) {
      throw new Error("rules-file must be relative to agent-path");
    }
    rulesFile = await resolveLocalRules(agentRoot, localRules);
    if ((await fs.stat(rulesFile)).size === 0) {
      throw new Error("rules-file must not be empty");
    }
  } else if (githubRules) {
    const rulesRoot = path.join(runtimeRoot, "rules");
    rulesFile = path.join(rulesRoot, "custom-rules.yaml");
    rulesEndpoint = parseGitHubRules(githubRules);
    await fs.mkdir(rulesRoot, { recursive: true });
  }

  const outputs = {
    "agent-root": agentRoot,
    "output-root": outputRoot,
    "report-id": reportId,
    "rules-endpoint": rulesEndpoint,
    "rules-file": rulesFile,
    "runtime-root": runtimeRoot,
  };
  await appendOutputs(outputFile, outputs);
  return outputs;
}

function escapeWorkflowCommand(value) {
  return String(value).replaceAll("%", "%25").replaceAll("\r", "%0D").replaceAll("\n", "%0A");
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  prepare().catch((error) => {
    console.error(`::error::${escapeWorkflowCommand(error.message)}`);
    process.exitCode = 1;
  });
}
