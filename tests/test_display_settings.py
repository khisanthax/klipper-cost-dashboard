import json
import os
import tempfile
import unittest


class DisplaySettingsTests(unittest.TestCase):
    def test_load_display_settings_maps_legacy_pause_exclude(self):
        from core.config import HEADERS
        from core.storage import load_display_settings

        with tempfile.TemporaryDirectory() as tmp:
            display_path = os.path.join(tmp, "display.json")
            with open(display_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "visible_columns": ["printer"],
                        "hidden_printers": [],
                        "pause_exclude_paused_time_default": True,  # legacy semantics
                    },
                    f,
                    indent=2,
                )

            loaded = load_display_settings(display_path, HEADERS)
            # Legacy exclude=True => include=False
            self.assertFalse(loaded.get("pause_include_paused_time_default", True))

    def test_save_display_settings_preserves_unknown_keys(self):
        from core.config import HEADERS, DISPLAY_FILE
        from core.storage import save_display_settings, load_display_settings

        with tempfile.TemporaryDirectory() as tmp:
            display_path = os.path.join(tmp, os.path.basename(DISPLAY_FILE))
            with open(display_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "visible_columns": ["printer"],
                        "hidden_printers": [],
                        # Legacy key (exclude semantics) should be handled gracefully.
                        "pause_exclude_paused_time_default": True,
                        "projects_show_cost_totals": True,
                        "unknown_key": "keep-me",
                    },
                    f,
                    indent=2,
                )

            save_display_settings(
                display_path,
                tmp,
                {
                    "visible_columns": ["printer", "job_uid"],  # job_uid should be stripped
                    "hidden_printers": ["SV08"],
                    "pause_include_paused_time_default": True,
                    "projects_show_cost_totals": False,
                },
            )

            with open(display_path, encoding="utf-8") as f:
                raw = json.load(f)

            self.assertEqual(raw.get("unknown_key"), "keep-me")
            self.assertTrue(raw.get("pause_include_paused_time_default"))
            self.assertFalse(raw.get("projects_show_cost_totals"))

            loaded = load_display_settings(display_path, HEADERS)
            self.assertIn("printer", loaded.get("visible_columns") or [])
            self.assertNotIn("job_uid", loaded.get("visible_columns") or [])
            # Ensure the returned dict uses the new include semantics key.
            self.assertIn("pause_include_paused_time_default", loaded)


if __name__ == "__main__":
    unittest.main()
