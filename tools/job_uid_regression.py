"""
Lightweight regression harness for stable history job identity (job_uid).

This script is safe by default (read-only). It can optionally run the
assignment migration if you pass --migrate.

Usage (from repo root):
  python3 tools/job_uid_regression.py
  python3 tools/job_uid_regression.py --migrate
"""

from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(description="KCD job_uid regression harness")
    parser.add_argument(
        "--migrate",
        action="store_true",
        help="Run assignment migration (writes data/project_assignments.json).",
    )
    args = parser.parse_args()

    from core.config import CSV_FILE
    from core.storage import load_rows_raw
    from core import projects

    rows, err = load_rows_raw(CSV_FILE)
    if err:
        print(f"ERROR: {err}")
        return 1

    missing_uid = [r for r in rows if not str(r.get("job_uid") or "").strip()]
    if missing_uid:
        print(f"FAIL: {len(missing_uid)} row(s) missing job_uid")
        return 2

    uids = [str(r.get("job_uid")) for r in rows]
    dupes = {u for u in uids if uids.count(u) > 1}
    if dupes:
        print(f"WARN: duplicate job_uid(s) detected: {sorted(list(dupes))[:10]}")
    else:
        print(f"OK: {len(rows)} row(s) have job_uid; no duplicates detected.")

    assignments = projects.load_assignments()
    legacy = [k for k in assignments.keys() if not str(k).startswith("job_")]
    if legacy:
        print(f"WARN: found {len(legacy)} legacy assignment key(s) (non-job_uid).")
        mapping = {}
        for r in rows:
            try:
                mapping[projects.job_key(r)] = r.get("job_uid")
            except Exception:
                continue
        unresolved = [k for k in legacy if k not in mapping]
        print(f" - resolvable: {len(legacy) - len(unresolved)}")
        print(f" - unresolved: {len(unresolved)}")
    else:
        print("OK: assignments use job_uid keys only.")

    if args.migrate:
        before = len(projects.load_assignments())
        projects.migrate_assignments_to_job_uid(rows)
        after = len(projects.load_assignments())
        print(f"MIGRATE: assignments entries before={before} after={after}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

