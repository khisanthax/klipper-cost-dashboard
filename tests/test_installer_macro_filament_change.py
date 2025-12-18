import os
import tempfile
import unittest


class FilamentChangeMacroPatchTests(unittest.TestCase):
    def test_insert_filament_change_macro_call_is_idempotent(self):
        import installer_macro

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "macros.cfg")
            with open(cfg_path, "w", encoding="utf-8") as f:
                f.write(
                    """
[gcode_macro M600]
gcode:
    RESPOND MSG="changing"
"""
                )

            ok, msg = installer_macro.insert_filament_change_macro_call(
                "M600",
                cfg_path,
                "KCD_JOB_PAUSE REASON=filament_change",
            )
            self.assertTrue(ok)

            ok2, msg2 = installer_macro.insert_filament_change_macro_call(
                "M600",
                cfg_path,
                "KCD_JOB_PAUSE REASON=filament_change",
            )
            self.assertTrue(ok2)
            self.assertEqual(msg2, "Call already present")

            with open(cfg_path, "r", encoding="utf-8") as f:
                text = f.read()

            self.assertEqual(text.count("KCD_JOB_PAUSE REASON=filament_change"), 1)
            self.assertIn(installer_macro.KCD_START_FILAMENT_CHANGE_MARKER, text)
            self.assertIn(installer_macro.KCD_END_FILAMENT_CHANGE_MARKER, text)

