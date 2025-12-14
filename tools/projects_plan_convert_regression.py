"""
Lightweight regression harness for converting a planned item -> manual job.

Run from repo root:
  python tools/projects_plan_convert_regression.py
"""

from __future__ import annotations

import os
import tempfile


def main() -> int:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(repo_root)

    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        os.makedirs("data", exist_ok=True)

        from core import projects

        p = projects.create_project("P", notes="")
        plan = projects.create_plan_item(
            project_id=p.id,
            filename="test.gcode",
            est_time_s=3600,
            est_filament_g=12.5,
            source="Test",
        )

        mj = projects.convert_plan_item_to_manual(project_id=p.id, plan_id=plan.plan_id)

        # Planned item should now be fulfilled and linked.
        plans = projects.load_plans().get(p.id, [])
        updated = [x for x in plans if x.plan_id == plan.plan_id][0]
        assert updated.status == "fulfilled"
        assert updated.converted_to_manual_job_id == mj.manual_job_id

        # Manual job fields should be populated.
        assert mj.project_id == p.id
        assert mj.title == "test.gcode"
        assert abs(mj.hours - 1.0) < 1e-9
        assert abs(mj.filament_g - 12.5) < 1e-9
        assert "Converted from planned item" in mj.notes
        assert plan.plan_id in mj.notes

        # Idempotency: second conversion blocked.
        try:
            projects.convert_plan_item_to_manual(project_id=p.id, plan_id=plan.plan_id)
            raise AssertionError("Expected second conversion to be blocked")
        except ValueError:
            pass

    print("OK: projects_plan_convert_regression")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

