"""Review binding and idle checks for the shared installer engine."""
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from installer.workflow import repair_client


class WebRepairEngineTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.config = self.root / "printer.cfg"
        self.config.write_text("[printer]\nkinematics: cartesian\n", encoding="utf-8")
        self.args = (self.root, "Fixture", "http://printer:7125", "http://master:6060", "test-only-credential")
        probe = {
            "config_dir": str(self.root),
            "identity": ("fixture", "instance", str(self.root)),
            "connected": True,
        }
        self.probe = self.start_patch("installer.workflow.probe_endpoint", return_value=probe)
        self.master = self.start_patch("installer.workflow.master_request", side_effect=self.master_response)
        self.opener = self.start_patch("installer.workflow.urllib.request.build_opener").return_value
        self.set_state("standby")

    def start_patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    @staticmethod
    def master_response(master, key, payload=None):
        if payload is None:
            return {"backend": "sql", "clients": []}
        return {"readiness": {"ready": True}}

    def set_state(self, state):
        self.opener.open.side_effect = lambda *args, **kwargs: StringIO(
            '{"result":{"status":{"print_stats":{"state":"' + state + '"}}}}'
        )

    def preview(self, **kwargs):
        return repair_client(*self.args, web_safe=True, **kwargs)

    def apply(self, revision, **kwargs):
        return repair_client(*self.args, web_safe=True, apply=True, expected_revision=revision, **kwargs)

    def test_review_is_repeatable_and_lists_only_file_actions(self):
        self.config.write_text("# private config comment\n", encoding="utf-8")
        preview = self.preview()
        self.assertFalse(preview["applied"])
        self.assertEqual(preview["revision"], self.preview()["revision"])
        self.assertIn("Update printer.cfg", preview["changes"])
        self.assertIn("Create kcd_job_start.sh", preview["changes"])
        self.assertNotIn("test-only-credential", str(preview))
        self.assertNotIn("private config comment", str(preview))
        self.assertEqual(set(self.root.iterdir()), {self.config})

        result = self.apply(preview["revision"])
        self.assertTrue(result["applied"])
        self.assertTrue((self.root / "print_cost.cfg").is_file())
        self.assertIn("[include print_cost.cfg]", self.config.read_text())
        self.assertEqual(self.opener.open.call_count, 2)
        with self.assertRaisesRegex(ValueError, "stale"):
            self.apply(preview["revision"])

    def test_active_include_change_invalidates_review(self):
        included = self.root / "custom.cfg"
        self.config.write_text("[include custom.cfg]\n", encoding="utf-8")
        included.write_text("# Before\n", encoding="utf-8")
        preview = self.preview()
        included.write_text("# Changed since preview\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "stale"):
            self.apply(preview["revision"])
        self.assertEqual(set(self.root.iterdir()), {self.config, included})
        self.assertTrue(all(len(call.args) == 2 for call in self.master.call_args_list))

    def test_relocation_cannot_be_added_after_preview(self):
        preview = self.preview()
        with self.assertRaisesRegex(ValueError, "stale"):
            self.apply(preview["revision"], relocate=True)
        self.assertEqual(set(self.root.iterdir()), {self.config})

    def test_apply_requires_review_and_known_inactive_state(self):
        with self.assertRaisesRegex(ValueError, "Review"):
            self.apply(None)
        self.master.assert_not_called()
        preview = self.preview()
        for state in ("printing", "paused", "error", "unknown"):
            with self.subTest(state=state):
                self.set_state(state)
                with self.assertRaisesRegex(ValueError, "inactive"):
                    self.apply(preview["revision"])
        self.assertEqual(set(self.root.iterdir()), {self.config})
        self.assertTrue(all(len(call.args) == 2 for call in self.master.call_args_list))

    def test_printer_that_starts_during_registration_blocks_file_changes(self):
        preview = self.preview()
        self.opener.open.side_effect = [
            StringIO('{"result":{"status":{"print_stats":{"state":"standby"}}}}'),
            StringIO('{"result":{"status":{"print_stats":{"state":"printing"}}}}'),
        ]
        with self.assertRaisesRegex(RuntimeError, "registration was retained. No client files were written"):
            self.apply(preview["revision"])
        self.assertEqual(set(self.root.iterdir()), {self.config})

    def test_refuses_include_outside_selected_configuration(self):
        self.config.write_text("[include ../elsewhere.cfg]\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "escapes the selected directory"):
            self.preview()
        self.assertEqual(set(self.root.iterdir()), {self.config})


if __name__ == "__main__":
    unittest.main()
