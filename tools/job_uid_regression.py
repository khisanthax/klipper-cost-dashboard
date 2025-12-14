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
import shutil
import tempfile


def main() -> int:
    parser = argparse.ArgumentParser(description="KCD job_uid regression harness")
    parser.add_argument(
        "--migrate",
        action="store_true",
        help="Run assignment migration (writes data/project_assignments.json).",
    )
    parser.add_argument(
        "--recalc",
        type=int,
        default=0,
        metavar="N",
        help="Run a safe recalc simulation on a temp CSV copy for N rows and verify job_uids don't change.",
    )
    args = parser.parse_args()

    from core.config import CSV_FILE, HEADERS
    from core.storage import load_rows_raw
    from core.storage import rewrite_csv_recalculate_costs_job_uids
    from core import projects
    from core.pricing import compute_costs

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
    def _looks_like_uid(k: str) -> bool:
        try:
            import uuid as _uuid
            _uuid.UUID(k)
            return True
        except Exception:
            return False

    legacy = [k for k in assignments.keys() if not _looks_like_uid(str(k))]
    if legacy:
        print(f"WARN: found {len(legacy)} legacy assignment key(s) (non-job_uid).")
        mapping = {}
        for r in rows:
            try:
                mapping[projects.job_key(r)] = r.get("job_uid")
                mapping[str(r.get("legacy_job_uid") or projects.compute_job_uid(r) or "").strip()] = r.get("job_uid")
            except Exception:
                continue
        unresolved = [k for k in legacy if k not in mapping]
        print(f" - resolvable: {len(legacy) - len(unresolved)}")
        print(f" - unresolved: {len(unresolved)}")
    else:
        print("OK: assignments use job_uid keys only.")

    if args.migrate:
        before_map = projects.load_assignments()
        before = len(before_map)
        legacy_before = [k for k in before_map.keys() if not _looks_like_uid(str(k))]
        _, orphans_added = projects.migrate_assignments_to_job_uid(rows)
        after_map = projects.load_assignments()
        after = len(after_map)
        legacy_after = [k for k in after_map.keys() if not _looks_like_uid(str(k))]

        print(f"MIGRATE: assignments entries before={before} after={after} orphans_added={orphans_added}")
        if legacy_after:
            print(f"FAIL: still have {len(legacy_after)} legacy assignment key(s) after migration.")
            return 9

        if legacy_before and orphans_added:
            # Optional sanity check: orphans file exists after cleanup.
            import os
            orphans_path = os.path.join(projects.DATA_DIR, "project_assignments_orphans.json")
            if not os.path.exists(orphans_path):
                print("FAIL: expected orphaned assignments file to exist after migration cleanup.")
                return 10

    if args.recalc:
        n = int(args.recalc or 0)
        if n <= 0:
            print("ERROR: --recalc requires N > 0")
            return 3

        tmpdir = tempfile.mkdtemp(prefix="kcd_jobuid_")
        try:
            tmp_csv = shutil.copy2(CSV_FILE, tempfile.mktemp(prefix="print_costs_", suffix=".csv", dir=tmpdir))

            before_rows, err2 = load_rows_raw(tmp_csv)
            if err2:
                print(f"ERROR: temp CSV load failed: {err2}")
                return 4

            selected = before_rows[:n]
            selected_uids = [str(r.get("job_uid") or "") for r in selected if str(r.get("job_uid") or "").strip()]
            if len(selected_uids) != len(selected):
                print("FAIL: some selected rows missing job_uid in temp copy")
                return 5

            before_sig = {uid: (r.get("timestamp_epoch"), r.get("printer"), r.get("filename")) for uid, r in zip(selected_uids, selected)}

            updated = rewrite_csv_recalculate_costs_job_uids(tmp_csv, HEADERS, selected_uids, compute_costs)
            print(f"SIM RECALC: updated={updated}")

            after_rows, err3 = load_rows_raw(tmp_csv)
            if err3:
                print(f"ERROR: temp CSV reload failed: {err3}")
                return 6

            after_map = {str(r.get("job_uid") or ""): r for r in after_rows}
            missing = [uid for uid in selected_uids if uid not in after_map]
            if missing:
                print(f"FAIL: some selected job_uids missing after recalc: {missing}")
                return 7

            changed = []
            for uid in selected_uids:
                r = after_map[uid]
                sig = (r.get("timestamp_epoch"), r.get("printer"), r.get("filename"))
                if sig != before_sig.get(uid):
                    changed.append(uid)
            if changed:
                print(f"FAIL: signatures changed for job_uid(s): {changed}")
                return 8

            print("OK: recalc simulation preserved job_uid and job identity fields.")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
