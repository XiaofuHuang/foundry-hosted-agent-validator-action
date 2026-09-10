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
        markdown=b"# unchanged\n",
        write_time_ns=1_000_000_000,
        rules=RULES,
    ):
        invocation.mkdir(parents=True, exist_ok=True)
        markdown_path = invocation / f"validation-{report_id}-{local_name}.md"
        json_path = invocation / f"validation-{report_id}-{local_name}.json"
        markdown_path.write_bytes(markdown)
        json_path.write_text(
            json.dumps(
                {
                    "reportId": report_id,
                    "generatedAt": "2026-09-10T12:00:00Z",
                    "target": {
                        "serviceName": service_name,
                        "agentRoot": ".",
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

    def test_shared_report_id_duplicate_normalization_and_markdown_rewrite(self):
        first = self.root / "first"
        second = self.root / "second"
        third = self.root / "third"
        self.stage_report(
            first,
            "Shared Agent",
            "shared-agent",
            markdown=b"# first report\n",
        )
        self.stage_report(
            second,
            "Shared Agent",
            "shared-agent",
            markdown=b"# second report\n",
        )
        self.stage_report(
            third,
            "Shared Agent",
            "shared-agent",
            markdown=b"# third report\n",
        )

        self.assertEqual(
            aggregate_invocation(first, self.output, self.state, REPORT_ID), 1
        )
        self.assertEqual(
            aggregate_invocation(second, self.output, self.state, REPORT_ID), 1
        )
        self.assertEqual(
            aggregate_invocation(third, self.output, self.state, REPORT_ID), 1
        )

        expected_names = ["shared-agent", "shared-agent-1", "shared-agent-2"]
        for name, markdown in zip(
            expected_names,
            (b"# first report\n", b"# second report\n", b"# third report\n"),
        ):
            final_markdown = (
                self.output / f"validation-{REPORT_ID}-{name}.md"
            ).resolve()
            final_json = self.output / f"validation-{REPORT_ID}-{name}.json"
            report = json.loads(final_json.read_text(encoding="utf-8"))
            self.assertEqual(report["reportId"], REPORT_ID)
            self.assertEqual(report["markdownPath"], str(final_markdown))
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

        self.assertEqual(
            aggregate_invocation(invocation, self.output, self.state, REPORT_ID), 2
        )

        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(state["usedNames"], ["first-name", "second-name"])

    def test_requires_byte_identical_merged_rules(self):
        first = self.root / "first"
        same = self.root / "same"
        different = self.root / "different"
        self.stage_report(first, "One", "one")
        self.stage_report(same, "Two", "two")
        self.stage_report(different, "Three", "three", rules=b"different: true\n")

        aggregate_invocation(first, self.output, self.state, REPORT_ID)
        aggregate_invocation(same, self.output, self.state, REPORT_ID)
        with self.assertRaisesRegex(AggregationError, "merged rules differ"):
            aggregate_invocation(different, self.output, self.state, REPORT_ID)
        self.assertFalse(
            (self.output / f"validation-{REPORT_ID}-three.json").exists()
        )

    def test_allows_invocation_with_no_hosted_reports(self):
        invocation = self.root / "empty"
        invocation.mkdir()

        self.assertEqual(
            aggregate_invocation(invocation, self.output, self.state, REPORT_ID), 0
        )
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
            aggregate_invocation(invocation, self.output, self.state, REPORT_ID)
        self.assertFalse(self.output.exists())
        self.assertTrue(json_path.exists())

    def test_rejects_malformed_json(self):
        invocation = self.root / "malformed"
        json_path, _ = self.stage_report(invocation, "Malformed", "malformed")
        json_path.write_text("{", encoding="utf-8")

        with self.assertRaisesRegex(AggregationError, "Cannot parse report JSON"):
            aggregate_invocation(invocation, self.output, self.state, REPORT_ID)

    def test_rejects_existing_destination_collision(self):
        invocation = self.root / "collision"
        self.stage_report(invocation, "Collision", "collision")
        self.output.mkdir()
        existing = self.output / f"validation-{REPORT_ID}-collision.json"
        existing.write_text("do not replace\n", encoding="utf-8")

        with self.assertRaisesRegex(AggregationError, "already exists"):
            aggregate_invocation(invocation, self.output, self.state, REPORT_ID)
        self.assertEqual(existing.read_text(encoding="utf-8"), "do not replace\n")

    def test_rejects_report_without_merged_rules(self):
        invocation = self.root / "missing-rules"
        self.stage_report(
            invocation, "Missing Rules", "missing-rules", rules=None
        )

        with self.assertRaisesRegex(AggregationError, "no merged-rules artifact"):
            aggregate_invocation(invocation, self.output, self.state, REPORT_ID)

    def test_rejects_mismatched_report_id_and_markdown_path(self):
        wrong_id = self.root / "wrong-id"
        self.stage_report(
            wrong_id, "Wrong ID", "wrong-id", report_id="different-report"
        )
        with self.assertRaisesRegex(AggregationError, "filename does not use reportId"):
            aggregate_invocation(wrong_id, self.output, self.state, REPORT_ID)

        wrong_path = self.root / "wrong-path"
        json_path, _ = self.stage_report(wrong_path, "Wrong Path", "wrong-path")
        report = json.loads(json_path.read_text(encoding="utf-8"))
        report["markdownPath"] = str((wrong_path / "other.md").resolve())
        json_path.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaisesRegex(AggregationError, "markdownPath does not match"):
            aggregate_invocation(wrong_path, self.output, self.state, REPORT_ID)

        wrong_json_id = self.root / "wrong-json-id"
        json_path, _ = self.stage_report(
            wrong_json_id, "Wrong JSON ID", "wrong-json-id"
        )
        report = json.loads(json_path.read_text(encoding="utf-8"))
        report["reportId"] = "different-report"
        json_path.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaisesRegex(AggregationError, "mismatched reportId"):
            aggregate_invocation(wrong_json_id, self.output, self.state, REPORT_ID)


if __name__ == "__main__":
    unittest.main()
