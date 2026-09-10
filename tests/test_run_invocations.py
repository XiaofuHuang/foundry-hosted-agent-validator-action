import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from scripts.run_invocations import InvocationError, SETTINGS, run_invocations


REPORT_ID = "20260910T220000Z"
FAKE_COPILOT = r"""#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path


def update_active(state_root, delta):
    lock = state_root / "lock"
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            time.sleep(0.005)
    try:
        state_path = state_root / "state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
        else:
            state = {"active": 0, "maxActive": 0}
        state["active"] += delta
        state["maxActive"] = max(state["maxActive"], state["active"])
        state_path.write_text(json.dumps(state), encoding="utf-8")
    finally:
        lock.rmdir()


arguments = sys.argv[1:]
workspace = Path(arguments[arguments.index("-C") + 1])
add_dirs = [
    arguments[index + 1]
    for index, value in enumerate(arguments)
    if value == "--add-dir"
]
output = Path(add_dirs[1])
state_root = Path(os.environ["TEST_STATE_ROOT"])
copilot_home = Path(os.environ["COPILOT_HOME"])
output.mkdir(parents=True, exist_ok=True)

update_active(state_root, 1)
try:
    (state_root / f"started-{workspace.name}.json").write_text(
        json.dumps({"pid": os.getpid()}), encoding="utf-8"
    )
    time.sleep(float(os.environ.get("TEST_DELAY", "0.15")))
    (output / "record.json").write_text(
        json.dumps(
            {
                "workspace": str(workspace),
                "copilotHome": str(copilot_home),
                "arguments": arguments,
                "hooks": os.environ[
                    "GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS"
                ],
                "autoUpdate": os.environ["COPILOT_AUTO_UPDATE"],
            }
        ),
        encoding="utf-8",
    )
    print(f"stdout:{workspace.name}")
    print(f"stderr:{workspace.name}", file=sys.stderr)
finally:
    update_active(state_root, -1)

raise SystemExit(7 if workspace.name == "fail" else 0)
"""


class RunInvocationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.template_home = self.root / "template-home"
        self.template_home.mkdir()
        (self.template_home / "settings.json").write_text(
            "template settings\n", encoding="utf-8"
        )
        (self.template_home / "template-marker").write_text(
            "copied\n", encoding="utf-8"
        )
        self.skill_root = self.root / "skill"
        self.skill_root.mkdir()
        self.fake_copilot = self.root / (
            "copilot" if os.name == "posix" else "fake_copilot.py"
        )
        self.fake_copilot.write_text(
            textwrap.dedent(FAKE_COPILOT), encoding="utf-8"
        )
        self.fake_copilot.chmod(0o755)
        self.state_root = self.root / "scheduler-state"
        self.state_root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def workspaces(self, *names):
        paths = []
        for name in names:
            path = self.root / "targets" / name
            path.mkdir(parents=True)
            paths.append(path)
        return paths

    def run_workers(self, paths, invocation_root, **kwargs):
        environment = dict(os.environ)
        environment.update(
            {
                "TEST_STATE_ROOT": str(self.state_root),
                "TEST_DELAY": "0.2",
            }
        )
        return run_invocations(
            paths,
            invocation_root,
            self.template_home,
            self.skill_root,
            REPORT_ID,
            max_workers=3,
            copilot_command=(sys.executable, str(self.fake_copilot)),
            environment=environment,
            poll_interval=0.01,
            **kwargs,
        )

    def test_caps_concurrency_and_isolates_worker_state_and_logs(self):
        paths = self.workspaces(
            "alpha", "bravo", "charlie", "delta", "echo", "foxtrot"
        )
        invocation_root = self.root / "invocations"

        self.assertEqual(self.run_workers(paths, invocation_root), [0] * len(paths))

        scheduler_state = json.loads(
            (self.state_root / "state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(scheduler_state["active"], 0)
        self.assertGreaterEqual(scheduler_state["maxActive"], 2)
        self.assertLessEqual(scheduler_state["maxActive"], 3)

        copilot_homes = []
        for index, workspace in enumerate(paths, start=1):
            worker_root = invocation_root / f"{index:06d}"
            record = json.loads(
                (worker_root / "output" / "record.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(record["workspace"], str(workspace))
            self.assertEqual(record["hooks"], "false")
            self.assertEqual(record["autoUpdate"], "false")
            prompt = record["arguments"][
                record["arguments"].index("--prompt") + 1
            ]
            self.assertIn(f"workspacePath={workspace}", prompt)
            self.assertIn(f"reportId={REPORT_ID}", prompt)
            self.assertIn("--allow-tool=write", record["arguments"])
            self.assertIn(
                "--available-tools=view,grep,glob,edit,apply_patch,create",
                record["arguments"],
            )
            self.assertIn("--deny-tool=shell", record["arguments"])
            self.assertIn("--deny-tool=url", record["arguments"])
            self.assertIn("--disable-builtin-mcps", record["arguments"])
            self.assertIn("--no-ask-user", record["arguments"])
            self.assertIn("--no-auto-update", record["arguments"])
            self.assertIn("--no-custom-instructions", record["arguments"])
            self.assertIn("--silent", record["arguments"])
            self.assertEqual(
                (worker_root / "copilot-home" / "settings.json").read_text(
                    encoding="utf-8"
                ),
                SETTINGS,
            )
            self.assertTrue(
                (worker_root / "copilot-home" / "template-marker").is_file()
            )
            self.assertEqual(
                (worker_root / "stdout.log").read_text(encoding="utf-8"),
                f"stdout:{workspace.name}\n",
            )
            self.assertEqual(
                (worker_root / "stderr.log").read_text(encoding="utf-8"),
                f"stderr:{workspace.name}\n",
            )
            self.assertEqual(
                (worker_root / "status").read_text(encoding="ascii"), "0\n"
            )
            copilot_homes.append(record["copilotHome"])

        self.assertEqual(len(set(copilot_homes)), len(paths))

    def test_propagates_failure_only_after_all_workers_are_reaped(self):
        paths = self.workspaces("alpha", "fail", "charlie", "delta", "echo")
        invocation_root = self.root / "failed-invocations"

        self.assertEqual(
            self.run_workers(paths, invocation_root), [0, 7, 0, 0, 0]
        )

        scheduler_state = json.loads(
            (self.state_root / "state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(scheduler_state["active"], 0)
        for index in range(1, len(paths) + 1):
            worker_root = invocation_root / f"{index:06d}"
            self.assertTrue((worker_root / "output" / "record.json").is_file())
            self.assertTrue((worker_root / "status").is_file())

    def test_passes_one_shared_caller_rules_file_to_every_worker(self):
        paths = self.workspaces("alpha", "bravo")
        invocation_root = self.root / "rules-invocations"
        rules_root = self.root / "rules"
        rules_root.mkdir()
        rules_file = rules_root / "custom-rules.yaml"
        rules_file.write_text("version: 1\nrules: []\n", encoding="utf-8")

        self.assertEqual(
            self.run_workers(
                paths,
                invocation_root,
                rules_root=rules_root,
                rules_file=rules_file,
                rules_kind="root",
            ),
            [0, 0],
        )

        for index in range(1, len(paths) + 1):
            record = json.loads(
                (
                    invocation_root
                    / f"{index:06d}"
                    / "output"
                    / "record.json"
                ).read_text(encoding="utf-8")
            )
            prompt = record["arguments"][
                record["arguments"].index("--prompt") + 1
            ]
            self.assertIn(
                f"rulesFile={rules_file} as the root batch rules file", prompt
            )
            self.assertIn(str(rules_root), record["arguments"])

    def test_rejects_worker_counts_above_fixed_limit(self):
        with self.assertRaisesRegex(InvocationError, "between 1 and 3"):
            run_invocations(
                [],
                self.root / "too-many",
                self.template_home,
                self.skill_root,
                REPORT_ID,
                max_workers=4,
            )

    @unittest.skipUnless(os.name == "posix", "process-group test requires POSIX")
    def test_cancellation_terminates_and_reaps_active_process_groups(self):
        paths = self.workspaces("alpha", "bravo", "charlie", "delta")
        invocation_root = self.root / "cancelled-invocations"
        invocation_list = self.root / "invocations.bin"
        invocation_list.write_bytes(
            b"".join(os.fsencode(path) + b"\0" for path in paths)
        )
        environment = dict(os.environ)
        environment.update(
            {
                "TEST_STATE_ROOT": str(self.state_root),
                "TEST_DELAY": "30",
            }
        )
        helper = Path(__file__).resolve().parents[1] / "scripts" / "run_invocations.py"
        process = subprocess.Popen(
            (
                sys.executable,
                str(helper),
                "--invocations-file",
                str(invocation_list),
                "--invocation-root",
                str(invocation_root),
                "--template-home",
                str(self.template_home),
                "--skill-root",
                str(self.skill_root),
                "--report-id",
                REPORT_ID,
                "--max-workers",
                "3",
            ),
            env={
                **environment,
                "PATH": str(self.root)
                + os.pathsep
                + environment.get("PATH", ""),
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        deadline = time.monotonic() + 10
        while (
            len(list(self.state_root.glob("started-*.json"))) < 3
            and process.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        started = list(self.state_root.glob("started-*.json"))
        self.assertEqual(len(started), 3)

        process.terminate()
        _, stderr = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 143, stderr.decode(errors="replace"))

        for started_path in started:
            pid = json.loads(started_path.read_text(encoding="utf-8"))["pid"]
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        statuses = sorted(invocation_root.glob("*/status"))
        self.assertEqual(len(statuses), 3)
        self.assertEqual(
            [path.read_text(encoding="ascii") for path in statuses],
            ["143\n", "143\n", "143\n"],
        )


class RunScriptContractTests(unittest.TestCase):
    def test_waits_for_success_before_ordered_aggregation(self):
        run_script = (
            Path(__file__).resolve().parents[1] / "run.sh"
        ).read_text(encoding="utf-8")

        scheduler = run_script.index('scripts/run_invocations.py"')
        wait = run_script.index('wait "$orchestrator_pid"', scheduler)
        success_gate = run_script.index(
            'if [[ "$orchestration_status" -ne 0 ]]', wait
        )
        failure_exit = run_script.index("  exit 1", success_gate)
        aggregation_loop = run_script.index("overall_status=0", success_gate)
        aggregator = run_script.index(
            'scripts/aggregate_reports.py"', aggregation_loop
        )
        ordered_input = run_script.index(
            'done < "$invocations_file"', aggregator
        )

        self.assertLess(scheduler, wait)
        self.assertLess(wait, success_gate)
        self.assertLess(failure_exit, aggregation_loop)
        self.assertLess(success_gate, aggregation_loop)
        self.assertLess(aggregation_loop, aggregator)
        self.assertLess(aggregator, ordered_input)


if __name__ == "__main__":
    unittest.main()
