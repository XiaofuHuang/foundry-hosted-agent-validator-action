const fs = require("node:fs");
const path = require("node:path");

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function regularFiles(root, pattern) {
  if (!root || !fs.existsSync(root)) {
    return [];
  }
  return fs
    .readdirSync(root, { withFileTypes: true })
    .filter((entry) => entry.isFile() && pattern.test(entry.name))
    .map((entry) => path.join(root, entry.name))
    .sort();
}

function copyOptionalReports(outputRoot, artifactRoot, reportId) {
  const escapedId = escapeRegExp(reportId);
  for (const jsonReport of regularFiles(
    outputRoot,
    new RegExp(`^validation-${escapedId}.*\\.json$`),
  )) {
    const stage = path.join(
      artifactRoot,
      `json-${path.basename(jsonReport, path.extname(jsonReport))}`,
    );
    fs.mkdirSync(stage, { recursive: true });
    fs.copyFileSync(jsonReport, path.join(stage, path.basename(jsonReport)));
  }

  for (const rulesFile of regularFiles(
    outputRoot,
    new RegExp(`^agent-validation-${escapedId}-rules\\.yaml$`),
  )) {
    const stage = path.join(artifactRoot, "rules");
    fs.mkdirSync(stage, { recursive: true });
    fs.copyFileSync(rulesFile, path.join(stage, path.basename(rulesFile)));
  }
}

async function postPullRequestComment(github, context, body) {
  if (context.eventName !== "pull_request") {
    return;
  }
  const issueNumber = context.payload.pull_request?.number;
  if (!issueNumber) {
    throw new Error("Pull request number is missing from the event payload");
  }
  await github.rest.issues.createComment({
    ...context.repo,
    issue_number: issueNumber,
    body,
  });
}

function failureComment(env) {
  return [
    "## Microsoft Foundry hosted-agent validation",
    "",
    "**Validation failed before any report was produced.**",
    "",
    `[View workflow run](${env.GITHUB_SERVER_URL}/${env.GITHUB_REPOSITORY}/actions/runs/${env.GITHUB_RUN_ID})`,
  ].join("\n");
}

async function publish({ github, context, core }, env = process.env) {
  const errors = [];
  const outcomes = [
    ["Set up Node.js", env.SETUP_OUTCOME],
    ["Install Copilot CLI", env.INSTALL_OUTCOME],
    ["Prepare validation", env.PREPARE_OUTCOME],
    ["Download validation skill", env.SKILL_OUTCOME],
    ["Validate hosted agent", env.VALIDATION_OUTCOME],
  ];
  for (const [name, outcome] of outcomes) {
    if (outcome !== "success") {
      errors.push(`${name} finished with outcome ${outcome || "unknown"}`);
    }
  }

  const reportId = env.REPORT_ID || "";
  const outputRoot = env.OUTPUT_ROOT || "";
  const artifactRoot =
    env.ARTIFACT_ROOT ||
    path.join(env.RUNNER_TEMP || ".", "foundry-validation-artifacts");
  fs.mkdirSync(artifactRoot, { recursive: true });

  let markdownReport;
  if (!reportId) {
    errors.push("Validation preparation did not produce a report ID");
  } else {
    const reports = regularFiles(
      outputRoot,
      new RegExp(`^validation-${escapeRegExp(reportId)}.*\\.md$`),
    );
    if (reports.length !== 1) {
      errors.push(`Expected exactly one Markdown report, found ${reports.length}`);
    } else if (fs.statSync(reports[0]).size === 0) {
      errors.push("The generated Markdown report is empty");
    } else {
      markdownReport = reports[0];
      const reportStage = path.join(artifactRoot, "report-1");
      fs.mkdirSync(reportStage, { recursive: true });
      fs.copyFileSync(
        markdownReport,
        path.join(reportStage, path.basename(markdownReport)),
      );
      copyOptionalReports(outputRoot, artifactRoot, reportId);
    }
  }

  try {
    const body = markdownReport
      ? fs.readFileSync(markdownReport, "utf8")
      : failureComment(env);
    await postPullRequestComment(github, context, body);
  } catch (error) {
    errors.push(`Failed to post PR comment: ${error.message}`);
  }

  core.setOutput("report-path", markdownReport || "");
  if (errors.length > 0) {
    core.setFailed(errors.join("\n"));
    return;
  }
  core.info("Generated one hosted-agent validation report.");
}

module.exports = publish;
module.exports.failureComment = failureComment;
module.exports.regularFiles = regularFiles;
