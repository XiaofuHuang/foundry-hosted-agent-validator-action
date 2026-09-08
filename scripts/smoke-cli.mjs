import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";


const binary = path.resolve(process.argv[2] ?? "");
if (!fs.existsSync(binary)) {
  console.error(`Copilot executable does not exist: ${binary}`);
  process.exit(1);
}

const constrainedArguments = [
  "--available-tools=view,grep,glob,edit,apply_patch,create",
  "--deny-tool=shell",
  "--deny-tool=url",
  "--disable-builtin-mcps",
];
const environment = {
  ...process.env,
  COPILOT_AUTO_UPDATE: "false",
  COPILOT_OFFLINE: "true",
};
const options = {
  encoding: "utf8",
  env: environment,
  shell: process.platform === "win32",
};

for (const terminalArgument of ["--help", "--version"]) {
  const result = spawnSync(binary, [...constrainedArguments, terminalArgument], options);
  if (result.status !== 0) {
    process.stderr.write(result.stderr || result.stdout || "Copilot CLI smoke failed\n");
    process.exit(result.status ?? 1);
  }
  if (terminalArgument === "--version" && !/\b1\.0\.83(?:[.-]\S+)?\b/.test(result.stdout)) {
    console.error(`Unexpected Copilot CLI version: ${result.stdout.trim()}`);
    process.exit(1);
  }
}

console.log("Copilot CLI 1.0.83 constrained-tool smoke passed without network access");
