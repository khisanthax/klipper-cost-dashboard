from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from installer.inspection import inspect_client


class InstallerInspectionTests(unittest.TestCase):
    def test_scoped_inspection_detects_effective_macros_and_duplicate_delegation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "printer.cfg").write_text("[include manual.cfg]\n", encoding="utf-8")
            (root / "manual.cfg").write_text(
                '[gcode_macro _KCD_VARS]\nvariable_printer_name: "Fixture"\ngcode:\n  G4\n'
                '[gcode_macro CHILD]\ngcode:\n  KCD_JOB_START\n'
                '[gcode_macro PRINT_START]\ngcode:\n  CHILD\n  KCD_JOB_START\n',
                encoding="utf-8",
            )
            (root / "auto.cfg").write_text("inactive secret value", encoding="utf-8")
            probe = {"connected": True, "config_dir": str(root)}
            instances = [{"kind": "klipper", "config": str(root / "printer.cfg")}]
            with patch("installer.inspection.probe_endpoint", return_value=probe), patch(
                "installer.inspection.process_instances", return_value=instances
            ):
                result = inspect_client(root, "http://fixture:7129")
            self.assertEqual(result["port"], 7129)
            self.assertEqual(result["printer_name"], "Fixture")
            self.assertEqual(result["unused_files"], ["auto.cfg"])
            self.assertTrue(result["active_process"])
            start = next(macro for macro in result["macros"] if macro["name"] == "PRINT_START")
            self.assertEqual(start["possible_duplicates"], ["start"])
            self.assertNotIn("inactive secret value", str(result))
            self.assertEqual(set(root.iterdir()), {root / name for name in ("printer.cfg", "manual.cfg", "auto.cfg")})

    def test_inspection_rejects_outside_includes_before_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "printer.cfg").write_text("[include ../outside.cfg]\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "escapes the selected"):
                inspect_client(root, "http://fixture:7125")
