import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.aggregate_reports import AggregationError, aggregate_invocation


REPORT_ID = "20260910T120000Z"
RULES = b"version: 1\nrules: []\n"


class AggregateReportsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.output = self.root / "aggregate"
        self.state = self.root / "state.json"

    def tearDown(self):
        self.temp.cleanup()

    def stage_report(
        self,
        invocation,
        service_name,
        local_name,
        *,
        report_id=REPORT_ID,
        agent_root=".",
        markdown=None,
        write_time_ns=1_000_000_000,
        rules=RULES,
    ):
        invocation.mkdir(parents=True, exist_ok=True)
        markdown_path = invocation / f"validation-{report_id}-{local_name}.md"
        json_path = invocation / f"validation-{report_id}-{local_name}.json"
        if markdown is None:
            markdown = self.report_markdown(agent_root)
        markdown_path.write_bytes(markdown)
        json_path.write_text(
            json.dumps(
                {
                    "reportId": report_id,
                    "generatedAt": "2026-09-10T12:00:00Z",
                    "target": {
                        "serviceName": service_name,
                        "agentRoot": agent_root,
                    },
                    "results": [],
                    "markdownPath": str(markdown_path.resolve()),
                }
            ),
            encoding="utf-8",
        )
        os.utime(json_path, ns=(write_time_ns, write_time_ns))
        if rules is not None:
            (invocation / f"agent-validation-{report_id}-rules.yaml").write_bytes(
                rules
            )
        return json_path, markdown_path

    @staticmethod
    def report_markdown(agent_root, label="unchanged"):
        return (
            "# Microsoft Foundry Agent Validation\r\n"
            f"marker: {label}\r\n"
            f"**Agent root:** {agent_root}<br>\r\n"
            "## Limitation\r\n"
        ).encode()

    def aggregate(self, invocation, *, agent_root="agents/fixture"):
        return aggregate_invocation(
            invocation, self.output, self.state, REPORT_ID, agent_root
        )

    def test_shared_report_id_duplicate_normalization_and_markdown_rewrite(self):
        first = self.root / "first"
        second = self.root / "second"
        third = self.root / "third"
        self.stage_report(
            first,
            "Shared Agent",
            "shared-agent",
            markdown=self.report_markdown(".", "first report"),
        )
        self.stage_report(
            second,
            "Shared Agent",
            "shared-agent",
            markdown=self.report_markdown(".", "second report"),
        )
        self.stage_report(
            third,
            "Shared Agent",
            "shared-agent",
            markdown=self.report_markdown(".", "third report"),
        )

        self.assertEqual(self.aggregate(first, agent_root="agents/first"), 1)
        self.assertEqual(self.aggregate(second, agent_root="agents/second"), 1)
        self.assertEqual(self.aggregate(third, agent_root="agents/third"), 1)

        expected_names = ["shared-agent", "shared-agent-1", "shared-agent-2"]
        for name, markdown in zip(
            expected_names,
            (
                self.report_markdown("agents/first", "first report"),
                self.report_markdown("agents/second", "second report"),
                self.report_markdown("agents/third", "third report"),
            ),
        ):
            final_markdown = (
                self.output / f"validation-{REPORT_ID}-{name}.md"
            ).resolve()
            final_json = self.output / f"validation-{REPORT_ID}-{name}.json"
            report = json.loads(final_json.read_text(encoding="utf-8"))
            self.assertEqual(report["reportId"], REPORT_ID)
            self.assertEqual(report["markdownPath"], str(final_markdown))
            self.assertEqual(
                report["target"]["agentRoot"],
                markdown.decode()
                .split("**Agent root:** ", 1)[1]
                .split("<br>", 1)[0],
            )
            self.assertEqual(final_markdown.read_bytes(), markdown)

        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(state["usedNames"], expected_names)
        self.assertEqual(
            sorted(path.name for path in self.output.glob("*-rules.yaml")),
            [f"agent-validation-{REPORT_ID}-rules.yaml"],
        )

    def test_preserves_serial_service_order_within_invocation(self):
        invocation = self.root / "multi"
        self.stage_report(
            invocation, "Second Name", "second-name", write_time_ns=2_000_000_000
        )
        self.stage_report(
            invocation, "First Name", "first-name", write_time_ns=1_000_000_000
        )

        self.assertEqual(self.aggregate(invocation), 2)

        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(state["usedNames"], ["first-name", "second-name"])

    def test_requires_byte_identical_merged_rules(self):
        first = self.root / "first"
        same = self.root / "same"
        different = self.root / "different"
        self.stage_report(first, "One", "one")
        self.stage_report(same, "Two", "two")
        self.stage_report(different, "Three", "three", rules=b"different: true\n")

        self.aggregate(first)
        self.aggregate(same)
        with self.assertRaisesRegex(AggregationError, "merged rules differ"):
            self.aggregate(different)
        self.assertFalse(
            (self.output / f"validation-{REPORT_ID}-three.json").exists()
        )

    def test_allows_invocation_with_no_hosted_reports(self):
        invocation = self.root / "empty"
        invocation.mkdir()

        self.assertEqual(self.aggregate(invocation), 0)
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(state["reportCount"], 0)
        self.assertEqual(list(self.output.glob("validation-*")), [])

    def test_rejects_incomplete_pair(self):
        invocation = self.root / "incomplete"
        json_path, markdown_path = self.stage_report(
            invocation, "Incomplete", "incomplete"
        )
        markdown_path.unlink()

        with self.assertRaisesRegex(AggregationError, "incomplete report pairs"):
            self.aggregate(invocation)
        self.assertFalse(self.output.exists())
        self.assertTrue(json_path.exists())

    def test_rejects_malformed_json(self):
        invocation = self.root / "malformed"
        json_path, _ = self.stage_report(invocation, "Malformed", "malformed")
        json_path.write_text("{", encoding="utf-8")

        with self.assertRaisesRegex(AggregationError, "Cannot parse report JSON"):
            self.aggregate(invocation)

    def test_rejects_existing_destination_collision(self):
        invocation = self.root / "collision"
        self.stage_report(invocation, "Collision", "collision")
        self.output.mkdir()
        existing = self.output / f"validation-{REPORT_ID}-collision.json"
        existing.write_text("do not replace\n", encoding="utf-8")

        with self.assertRaisesRegex(AggregationError, "already exists"):
            self.aggregate(invocation)
        self.assertEqual(existing.read_text(encoding="utf-8"), "do not replace\n")

    def test_rejects_report_without_merged_rules(self):
        invocation = self.root / "missing-rules"
        self.stage_report(
            invocation, "Missing Rules", "missing-rules", rules=None
        )

        with self.assertRaisesRegex(AggregationError, "no merged-rules artifact"):
            self.aggregate(invocation)

    def test_rejects_mismatched_report_id_and_markdown_path(self):
        wrong_id = self.root / "wrong-id"
        self.stage_report(
            wrong_id, "Wrong ID", "wrong-id", report_id="different-report"
        )
        with self.assertRaisesRegex(AggregationError, "filename does not use reportId"):
            self.aggregate(wrong_id)

        wrong_path = self.root / "wrong-path"
        json_path, _ = self.stage_report(wrong_path, "Wrong Path", "wrong-path")
        report = json.loads(json_path.read_text(encoding="utf-8"))
        report["markdownPath"] = str((wrong_path / "other.md").resolve())
        json_path.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaisesRegex(AggregationError, "markdownPath does not match"):
            self.aggregate(wrong_path)

        wrong_json_id = self.root / "wrong-json-id"
        json_path, _ = self.stage_report(
            wrong_json_id, "Wrong JSON ID", "wrong-json-id"
        )
        report = json.loads(json_path.read_text(encoding="utf-8"))
        report["reportId"] = "different-report"
        json_path.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaisesRegex(AggregationError, "mismatched reportId"):
            self.aggregate(wrong_json_id)

    def test_rewrites_all_invocation_agent_root_forms_consistently(self):
        originals = (
            ".",
            str((self.root / "workspace" / "agents" / "absolute").resolve()),
            "already/relative",
        )
        stable_agent_root = "agents/stable-fixture"

        for index, original_agent_root in enumerate(originals):
            with self.subTest(original_agent_root=original_agent_root):
                invocation = self.root / f"root-form-{index}"
                output = self.root / f"aggregate-{index}"
                state = self.root / f"state-{index}.json"
                _, source_markdown = self.stage_report(
                    invocation,
                    "Stable Fixture",
                    "stable-fixture",
                    agent_root=original_agent_root,
                    markdown=self.report_markdown(original_agent_root, f"case-{index}"),
                )

                self.assertEqual(
                    aggregate_invocation(
                        invocation,
                        output,
                        state,
                        REPORT_ID,
                        stable_agent_root,
                    ),
                    1,
                )

                final_json = output / f"validation-{REPORT_ID}-stable-fixture.json"
                final_markdown = output / f"validation-{REPORT_ID}-stable-fixture.md"
                report = json.loads(final_json.read_text(encoding="utf-8"))
                self.assertEqual(report["target"]["agentRoot"], stable_agent_root)
                self.assertEqual(
                    final_markdown.read_bytes(),
                    source_markdown.read_bytes().replace(
                        f"**Agent root:** {original_agent_root}<br>".encode(),
                        f"**Agent root:** {stable_agent_root}<br>".encode(),
                    ),
                )

    def test_rejects_markdown_agent_root_mismatch(self):
        invocation = self.root / "root-mismatch"
        self.stage_report(
            invocation,
            "Root Mismatch",
            "root-mismatch",
            agent_root=".",
            markdown=self.report_markdown("different/root"),
        )

        with self.assertRaisesRegex(AggregationError, "does not match its JSON pair"):
            self.aggregate(invocation)


if __name__ == "__main__":
    unittest.main()
