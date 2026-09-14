import { createHash } from "node:crypto";
import dns from "node:dns/promises";
import fs from "node:fs/promises";
import https from "node:https";
import net from "node:net";
import path from "node:path";
import { pathToFileURL } from "node:url";

const MAX_RULES_BYTES = 1024 * 1024;
const CONNECT_TIMEOUT_MS = 10_000;
const TRANSFER_TIMEOUT_MS = 30_000;

function requireValue(env, name) {
  const value = env[name];
  if (!value) {
    throw new Error(`${name} must not be empty`);
  }
  return value;
}

function isInside(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === "" || (!relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative));
}

function ipv4ToNumber(address) {
  return address
    .split(".")
    .reduce((value, part) => ((value << 8) | Number(part)) >>> 0, 0);
}

function isInSubnet(value, base, prefix) {
  const mask = prefix === 0 ? 0 : (0xffffffff << (32 - prefix)) >>> 0;
  return (value & mask) >>> 0 === (ipv4ToNumber(base) & mask) >>> 0;
}

export function isPublicIPv4(address) {
  if (net.isIP(address) !== 4) {
    return false;
  }

  const value = ipv4ToNumber(address);
  const blockedSubnets = [
    ["0.0.0.0", 8],
    ["10.0.0.0", 8],
    ["100.64.0.0", 10],
    ["127.0.0.0", 8],
    ["169.254.0.0", 16],
    ["172.16.0.0", 12],
    ["192.0.0.0", 24],
    ["192.0.2.0", 24],
    ["192.88.99.0", 24],
    ["192.168.0.0", 16],
    ["198.18.0.0", 15],
    ["198.51.100.0", 24],
    ["203.0.113.0", 24],
    ["224.0.0.0", 4],
    ["240.0.0.0", 4],
  ];
  return !blockedSubnets.some(([base, prefix]) => isInSubnet(value, base, prefix));
}

export function parseRemoteRulesUrl(value) {
  if (!/^[\x00-\x7f]+$/.test(value)) {
    throw new Error("Remote rules-file URL must be ASCII");
  }

  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error("Remote rules-file must be a valid HTTPS URL");
  }
  if (url.protocol !== "https:" || !url.hostname) {
    throw new Error("Remote rules-file must use HTTPS");
  }
  if (url.username || url.password) {
    throw new Error("Remote rules-file URL must not contain credentials");
  }
  return url;
}

async function requestRules(url, address) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let totalBytes = 0;
    let settled = false;

    const finish = (error, value) => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(connectTimer);
      clearTimeout(transferTimer);
      if (error) {
        reject(error);
      } else {
        resolve(value);
      }
    };

    const request = https.request(
      {
        protocol: "https:",
        hostname: url.hostname,
        port: url.port || 443,
        path: `${url.pathname}${url.search}`,
        method: "GET",
        headers: {
          Host: url.host,
          "User-Agent": "foundry-hosted-agent-validator-action",
        },
        agent: false,
        servername: url.hostname,
        lookup(_hostname, options, callback) {
          if (options?.all) {
            callback(null, [{ address, family: 4 }]);
          } else {
            callback(null, address, 4);
          }
        },
      },
      (response) => {
        if (response.statusCode !== 200) {
          response.resume();
          finish(new Error("Remote rules-file must return HTTP 200 without redirects"));
          return;
        }

        const contentLength = Number(response.headers["content-length"] || 0);
        if (contentLength > MAX_RULES_BYTES) {
          response.resume();
          finish(new Error("Remote rules-file must not exceed 1 MiB"));
          return;
        }

        response.on("data", (chunk) => {
          totalBytes += chunk.length;
          if (totalBytes > MAX_RULES_BYTES) {
            request.destroy(new Error("Remote rules-file must not exceed 1 MiB"));
            return;
          }
          chunks.push(chunk);
        });
        response.on("end", () => finish(null, Buffer.concat(chunks)));
        response.on("error", (error) => finish(error));
      },
    );

    const connectTimer = setTimeout(
      () => request.destroy(new Error("Remote rules-file connection timed out")),
      CONNECT_TIMEOUT_MS,
    );
    const transferTimer = setTimeout(
      () => request.destroy(new Error("Remote rules-file transfer timed out")),
      TRANSFER_TIMEOUT_MS,
    );
    request.on("socket", (socket) => {
      if (socket.connecting) {
        socket.once("secureConnect", () => clearTimeout(connectTimer));
      } else {
        clearTimeout(connectTimer);
      }
    });
    request.on("error", (error) => finish(error));
    request.end();
  });
}

export async function downloadRemoteRules(value, destination) {
  const url = parseRemoteRulesUrl(value);
  const addresses = await dns.lookup(url.hostname, {
    family: 4,
    all: true,
    verbatim: true,
  });
  const publicAddresses = addresses
    .map(({ address }) => address)
    .filter(isPublicIPv4)
    .sort();
  if (publicAddresses.length === 0) {
    throw new Error("Remote rules-file host must resolve to a public IPv4 address");
  }

  const contents = await requestRules(url, publicAddresses[0]);
  await fs.mkdir(path.dirname(destination), { recursive: true });
  await fs.writeFile(destination, contents);
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

export async function prepare(env = process.env, dependencies = {}) {
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
  const outputRoot = path.join(runtimeRoot, "output");
  const artifactRoot = path.join(runnerTemp, "foundry-validation-artifacts", reportId);
  const copilotHome = path.join(runtimeRoot, "copilot-home");
  const sourceRepo = path.join(runtimeRoot, "github-copilot-for-azure");
  const skillParent = path.join(runtimeRoot, "skills");
  const skillRoot = path.join(skillParent, "validate-foundry-ci");
  const promptFile = path.join(runtimeRoot, "prompt.txt");
  await Promise.all([
    fs.mkdir(outputRoot, { recursive: true }),
    fs.mkdir(artifactRoot, { recursive: true }),
    fs.mkdir(copilotHome, { recursive: true }),
  ]);

  let rulesFile = "";
  let rulesRoot = "";
  const rulesInput = env.INPUT_RULES_FILE || "";
  if (rulesInput) {
    if (rulesInput.startsWith("https://")) {
      rulesRoot = path.join(runtimeRoot, "rules");
      rulesFile = path.join(rulesRoot, "custom-rules.yaml");
      const downloader = dependencies.downloadRemoteRules || downloadRemoteRules;
      await downloader(rulesInput, rulesFile);
    } else if (rulesInput.includes("://") || path.isAbsolute(rulesInput)) {
      throw new Error("rules-file must be relative to agent-path or use public HTTPS");
    } else {
      rulesFile = await resolveLocalRules(agentRoot, rulesInput);
    }

    if ((await fs.stat(rulesFile)).size === 0) {
      throw new Error("rules-file must not be empty");
    }
  }

  const rulesPrompt = rulesFile
    ? ` Use rulesFile=${rulesFile} as the explicit caller rules file.`
    : "";
  const prompt = [
    `Use the /validate-foundry-ci skill with agentPath=${agentRoot}, outputPath=${outputRoot}, and reportId=${reportId}.`,
    "Run the downloaded validation workflow exactly once for this agent, write one",
    `report pair under outputPath, and return the report paths.${rulesPrompt}`,
  ].join("\n");
  await fs.writeFile(promptFile, prompt);

  const outputs = {
    "agent-root": agentRoot,
    "artifact-name": `foundry-validation-${reportId}`,
    "artifact-root": artifactRoot,
    "copilot-home": copilotHome,
    "output-root": outputRoot,
    "prompt-file": promptFile,
    "report-id": reportId,
    "rules-file": rulesFile,
    "rules-root": rulesRoot,
    "skill-parent": skillParent,
    "skill-root": skillRoot,
    "source-repo": sourceRepo,
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
