import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";


class TargetError extends Error {}


function parseArguments(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined) {
      throw new TargetError("Invalid command-line arguments");
    }
    values[key.slice(2)] = value;
  }
  for (const required of ["agent-path", "workspace", "runtime-root", "github-output"]) {
    if (!values[required]) {
      throw new TargetError(`Missing --${required}`);
    }
  }
  return values;
}


function ensureInside(candidate, workspace) {
  const relative = path.relative(workspace, candidate);
  if (relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new TargetError("agent-path must remain inside GITHUB_WORKSPACE");
  }
  return relative;
}


function rejectSymlinkedPath(candidate, workspace) {
  const relative = ensureInside(candidate, workspace);
  let current = workspace;
  for (const component of relative.split(path.sep).filter(Boolean)) {
    current = path.join(current, component);
    if (fs.lstatSync(current).isSymbolicLink()) {
      throw new TargetError("agent-path must not contain symbolic links");
    }
  }
}


export function resolveTarget(agentPath, workspace, runtimeRoot) {
  const workspaceRoot = fs.realpathSync(workspace);
  const candidate = path.resolve(workspaceRoot, agentPath);
  rejectSymlinkedPath(candidate, workspaceRoot);
  const root = fs.realpathSync(candidate);
  if (!fs.statSync(root).isDirectory()) {
    throw new TargetError("agent-path must identify a directory");
  }

  const azureYaml = path.join(root, "azure.yaml");
  const yamlStat = fs.lstatSync(azureYaml);
  if (yamlStat.isSymbolicLink() || !yamlStat.isFile()) {
    throw new TargetError("azure.yaml must be a regular file");
  }
  if (yamlStat.size > 2 * 1024 * 1024) {
    throw new TargetError("azure.yaml is unexpectedly large");
  }

  const require = createRequire(path.join(path.resolve(runtimeRoot), "package.json"));
  const YAML = require("yaml");
  const document = YAML.parseDocument(fs.readFileSync(azureYaml, "utf8"), {
    customTags: [],
    maxAliasCount: 0,
    merge: false,
    prettyErrors: false,
    schema: "core",
    uniqueKeys: true,
  });
  if (document.errors.length > 0) {
    throw new TargetError(`azure.yaml is invalid YAML: ${document.errors[0].message}`);
  }
  const parsed = document.toJS({ maxAliasCount: 0, mapAsMap: false });
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new TargetError("azure.yaml must contain a mapping");
  }
  const services = parsed.services;
  if (!services || typeof services !== "object" || Array.isArray(services)) {
    throw new TargetError("azure.yaml must contain a services mapping");
  }

  const selected = Object.entries(services)
    .filter(([, service]) => (
      service
      && typeof service === "object"
      && !Array.isArray(service)
      && Object.prototype.hasOwnProperty.call(service, "host")
      && service.host === "azure.ai.agent"
    ))
    .map(([name]) => name);
  if (selected.length !== 1) {
    throw new TargetError(
      `Expected exactly one azure.ai.agent service in azure.yaml; found ${selected.length}`,
    );
  }
  if (!/^[A-Za-z0-9_.-]+$/.test(selected[0])) {
    throw new TargetError("Selected service name contains unsupported characters");
  }
  return { root, service: selected[0] };
}


function appendOutput(outputPath, key, value) {
  if (/[\r\n]/.test(value)) {
    throw new TargetError(`Unsafe newline in ${key}`);
  }
  fs.appendFileSync(outputPath, `${key}=${value}\n`, "utf8");
}


function main() {
  const args = parseArguments(process.argv.slice(2));
  const target = resolveTarget(args["agent-path"], args.workspace, args["runtime-root"]);
  appendOutput(args["github-output"], "agent-root", target.root);
  appendOutput(args["github-output"], "service-name", target.service);
}


try {
  main();
} catch (error) {
  const message = String(error?.message ?? error)
    .replaceAll("%", "%25")
    .replaceAll("\r", " ")
    .replaceAll("\n", " ");
  console.error(`::error::Hosted-agent target validation failed: ${message}`);
  process.exitCode = 1;
}
