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

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export async function regularFiles(root, pattern) {
  let entries;
  try {
    entries = await fs.readdir(root, { withFileTypes: true });
  } catch (error) {
    if (error.code === "ENOENT") {
      return [];
    }
    throw error;
  }
  return entries
    .filter((entry) => entry.isFile() && pattern.test(entry.name))
    .map((entry) => path.join(root, entry.name))
    .sort();
}

async function appendOutputs(outputFile, outputs) {
  const content = Object.entries(outputs)
    .map(([name, value]) => `${name}=${value}`)
    .join("\n");
  await fs.appendFile(outputFile, `${content}\n`);
}

async function copyOptionalReports(outputRoot, artifactRoot, escapedId) {
  const jsonReports = await regularFiles(
    outputRoot,
    new RegExp(`^validation-${escapedId}.*\\.json$`),
  );
  for (const jsonReport of jsonReports) {
    const stage = path.join(
      artifactRoot,
      `json-${path.basename(jsonReport, path.extname(jsonReport))}`,
    );
    await fs.mkdir(stage, { recursive: true });
    await fs.copyFile(jsonReport, path.join(stage, path.basename(jsonReport)));
  }

  const rulesFiles = await regularFiles(
    outputRoot,
    new RegExp(`^agent-validation-${escapedId}-rules\\.yaml$`),
  );
  for (const rulesFile of rulesFiles) {
    const stage = path.join(artifactRoot, "rules");
    await fs.mkdir(stage, { recursive: true });
    await fs.copyFile(rulesFile, path.join(stage, path.basename(rulesFile)));
  }
}

export async function collect(env = process.env) {
  const reportId = requireValue(env, "REPORT_ID");
  const outputRoot = requireValue(env, "OUTPUT_ROOT");
  const artifactRoot = requireValue(env, "ARTIFACT_ROOT");
  const outputFile = requireValue(env, "GITHUB_OUTPUT");
  const escapedId = escapeRegExp(reportId);
  const markdownReports = await regularFiles(
    outputRoot,
    new RegExp(`^validation-${escapedId}.*\\.md$`),
  );

  let reportPath = "";
  let errorMessage = "";
  if (markdownReports.length !== 1) {
    errorMessage = `Expected exactly one Markdown report, found ${markdownReports.length}`;
  } else if ((await fs.stat(markdownReports[0])).size === 0) {
    errorMessage = "The generated Markdown report is empty";
  } else {
    reportPath = markdownReports[0];
    const reportStage = path.join(artifactRoot, "report-1");
    await fs.mkdir(reportStage, { recursive: true });
    await fs.copyFile(
      reportPath,
      path.join(reportStage, path.basename(reportPath)),
    );
    await copyOptionalReports(outputRoot, artifactRoot, escapedId);
  }

  await appendOutputs(outputFile, {
    "report-count": markdownReports.length,
    "report-path": reportPath,
  });
  if (errorMessage) {
    throw new Error(errorMessage);
  }
  console.log("Collected one hosted-agent validation report.");
  return { reportPath };
}

function escapeWorkflowCommand(value) {
  return String(value).replaceAll("%", "%25").replaceAll("\r", "%0D").replaceAll("\n", "%0A");
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  collect().catch((error) => {
    console.error(`::error::${escapeWorkflowCommand(error.message)}`);
    process.exitCode = 1;
  });
}
