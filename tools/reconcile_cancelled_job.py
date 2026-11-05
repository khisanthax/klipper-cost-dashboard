"""Preview a Moonraker cancellation recovery. Apply only with the KCD writer stopped."""

import argparse
import hashlib
import json
from contextlib import closing
from pathlib import Path
import sys
from urllib.parse import urlencode
from urllib.request import urlopen
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import db, system_events
from core.import_moonraker import IMPORT_SOURCE_MOONRAKER_HISTORY, compute_import_id
from core.numeric import finite_float
from core.sql_only import is_sql_only


def get(base, path):
    with urlopen(base.rstrip("/") + path, timeout=5) as response:
        return json.load(response)["result"]


def preview(conn, printer, history_id, archived_retained=None):
    row = conn.execute(
        "SELECT id, moonraker_url FROM printers WHERE name=?", (printer,)
    ).fetchone()
    if not row or not row["moonraker_url"]:
        raise ValueError("Printer must have a registered Moonraker URL")
    base = row["moonraker_url"]
    status = get(base, "/printer/objects/query?print_stats&gcode_macro%20_KCD_VARS")["status"]
    if status["gcode_macro _KCD_VARS"]["printer_name"] != printer:
        raise ValueError("Loaded printer identity differs")
    if status["print_stats"]["state"] not in {"standby", "cancelled", "complete", "error"}:
        raise ValueError("Printer is active or state is unknown")
    job = get(base, "/server/history/job?" + urlencode({"uid": history_id}))["job"]
    if job["job_id"] != history_id or job["status"] != "cancelled":
        raise ValueError("Expected a cancelled Moonraker history record")

    live_row = conn.execute("SELECT value_json FROM user_settings WHERE key='live_jobs'").fetchone()
    live = json.loads(live_row[0]) if live_row else {"jobs": {}}
    current = live["jobs"].get(printer)
    retained = archived_retained if archived_retained is not None else current
    import_id = compute_import_id(printer, job)
    uid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{IMPORT_SOURCE_MOONRAKER_HISTORY}:{import_id}"))
    existing = conn.execute("SELECT * FROM jobs WHERE job_uid=?", (uid,)).fetchone()
    if existing:
        if (existing["printer_id"], existing["filename"], existing["status"], existing["import_id"]) != (
            row["id"], job["filename"], "canceled", import_id
        ):
            raise ValueError("Existing history conflicts with this recovery")
        return {"already_recorded": uid}
    if not retained or retained["printer_name"] != printer or retained["filename"] != job["filename"]:
        raise ValueError("Retained job identity does not match")
    start = finite_float(job["start_time"], label="start", nonnegative=True)
    end = finite_float(job["end_time"], label="end", nonnegative=True)
    if end <= start or abs(float(retained["start_time"]) - start) > 2:
        raise ValueError("Retained start does not match this Moonraker attempt")
    candidates = get(base, "/server/history/list?limit=200&order=desc")["jobs"]
    matches = [
        entry["job_id"] for entry in candidates
        if entry["filename"] == job["filename"] and abs(entry["start_time"] - retained["start_time"]) <= 2
    ]
    if matches != [history_id]:
        raise ValueError("Moonraker attempt is ambiguous or outside recent history")
    conflict = conn.execute(
        """SELECT 1 FROM jobs WHERE printer_id=? AND
           (import_id=? OR (filename=? AND (
            ABS(CAST(strftime('%s',started_at) AS INTEGER)-?) <= 2 OR ABS(CAST(strftime('%s',ended_at) AS INTEGER)-?) <= 2)))""",
        (row["id"], import_id, job["filename"], start, end),
    ).fetchone()
    if conflict:
        raise ValueError("Potential existing KCD history requires manual review")
    duration = finite_float(job["print_duration"], label="duration", nonnegative=True)
    filament = finite_float(job["filament_used"], label="filament", nonnegative=True)
    return {
        "before": retained,
        "current_live": current,
        "clear_live": current == retained,
        "moonraker": {key: job[key] for key in (
            "job_id", "status", "start_time", "end_time", "print_duration", "total_duration", "filament_used"
        )},
        "after": {
            "job_uid": uid, "printer": printer, "filename": job["filename"],
            "started_at": start, "ended_at": end, "status": "canceled", "job_outcome": "canceled",
            "duration_seconds": duration, "duration_seconds_raw": duration,
            "duration_seconds_effective": duration, "duration_hours": duration / 3600,
            "filament_mm": filament, "filament_mm_raw": filament,
            "filament_mm_effective": filament, "filament_meters": filament / 1000,
            "thumbnail_asset": retained.get("thumbnail_asset"),
            "import_source": IMPORT_SOURCE_MOONRAKER_HISTORY, "import_id": import_id,
            "failure_reason": "Recovered from Moonraker cancellation history. Historical pricing unavailable.",
            "time_cost": None, "material_cost": None, "total_cost": None,
        },
    }


def apply(conn, plan):
    after = plan["after"]
    printer = after["printer"]
    previous_updated_at = conn.execute(
        "SELECT updated_at FROM printers WHERE name=?", (printer,)
    ).fetchone()[0]
    db.upsert_job(conn, after)
    conn.execute("UPDATE printers SET updated_at=? WHERE name=?", (previous_updated_at, printer))
    if plan["clear_live"]:
        live = json.loads(conn.execute(
            "SELECT value_json FROM user_settings WHERE key='live_jobs'"
        ).fetchone()[0])
        if live["jobs"].get(printer) != plan["before"]:
            raise ValueError("Retained job changed after preview")
        del live["jobs"][printer]
        conn.execute("UPDATE user_settings SET value_json=? WHERE key='live_jobs'", (json.dumps(live),))
    system_events.emit_event(
        "activity", "Recovered cancelled print",
        "Matched retained KCD evidence to a unique Moonraker cancellation. Historical cost was unavailable.",
        meta=plan, connection=conn,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("printer")
    parser.add_argument("history_id")
    parser.add_argument("--archived-retained", type=Path, help="Trusted incident snapshot containing retained job JSON")
    parser.add_argument("--apply", metavar="PREVIEW_SHA256", help="Confirm preview while KCD is stopped")
    args = parser.parse_args()
    if not is_sql_only():
        parser.error("Cancellation recovery requires SQL-only mode")
    retained = json.loads(args.archived_retained.read_text(encoding="utf-8"))["retained"] if args.archived_retained else None
    with closing(db.connect_db()) as conn, conn:
        if args.apply:
            conn.execute("BEGIN IMMEDIATE")
        plan = preview(conn, args.printer, args.history_id, retained)
        digest = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
        if args.apply and "after" in plan:
            if args.apply != digest:
                parser.error("Preview changed. Generate a new preview.")
            apply(conn, plan)
        print(json.dumps({"preview_sha256": digest, "applied": bool(args.apply and "after" in plan), **plan}, indent=2))


if __name__ == "__main__":
    main()
