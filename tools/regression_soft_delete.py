"""
Lightweight regression test for printer soft-delete (hide) behavior.

Run from repo root:
  python tools/regression_soft_delete.py
"""

from __future__ import annotations

import csv
import os
import sys
import tempfile
from pathlib import Path


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = [
        "timestamp",
        "printer",
        "filename",
        "duration_seconds",
        "duration_hours",
        "filament_mm",
        "filament_meters",
        "rate_per_hour",
        "filament_mode",
        "filament_rate",
        "grams_per_meter",
        "time_cost",
        "material_cost",
        "total_cost",
        "filament_profile_id",
        "filament_material",
        "status",
        "failure_reason",
    ]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))

    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        data_dir = Path("data")
        data_dir.mkdir(parents=True, exist_ok=True)

        # Create minimal settings + display + csv in this temp cwd.
        # Include a key with surrounding whitespace to ensure delete/hide normalize names.
        (data_dir / "settings.json").write_text('{"P1 ": {}}', encoding="utf-8")
        (data_dir / "display.json").write_text('{"visible_columns": ["printer"], "hidden_printers": []}', encoding="utf-8")
        _write_csv(
            data_dir / "print_costs.csv",
            [
                 {"printer": "P1", "filename": "a.gcode"},
                 {"printer": "P2  ", "filename": "b.gcode"},
             ],
         )

        from core import pricing

        assert "P1" in pricing.get_known_printers()
         assert "P2" in pricing.get_known_printers()

        # Soft delete for printer in settings + csv.
         pricing.delete_printer("P1", delete_csv=False)
         pricing.hide_printer("P1")
         assert "P1" not in pricing.get_configured_printers()
         assert "P1" not in pricing.get_known_printers()

        # CSV row remains.
        csv_text = (data_dir / "print_costs.csv").read_text(encoding="utf-8")
        assert "P1" in csv_text

        # Soft delete for printer only in csv.
         pricing.hide_printer("P2")
         assert "P2" not in pricing.get_discovered_printers()
         assert "P2" not in pricing.get_known_printers()

        # CSV row remains.
        csv_text = (data_dir / "print_costs.csv").read_text(encoding="utf-8")
        assert "P2" in csv_text

    print("OK: regression_soft_delete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
