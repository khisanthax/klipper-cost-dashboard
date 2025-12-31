"""
Parity checks between CSV and SQLite storage.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict

from core import db as db_module
from core.config import CSV_FILE, DATA_DIR, HEADERS
from core.storage import ensure_csv_schema


VERIFY_REPORT_PATH = os.path.join(DATA_DIR, "verify_report.json")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_timestamp(value: object) -> Optional[float]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return float(raw)
    except Exception:
        pass
    try:
        cleaned = raw.replace("Z", "").replace("T", " ")
        return datetime.fromisoformat(cleaned).timestamp()
    except Exception:
        return None


def _load_csv_rows() -> list[dict]:
    if not os.path.exists(CSV_FILE):
        return []
    try:
        ensure_csv_schema(CSV_FILE, HEADERS)
    except Exception:
        pass
    rows = []
    with open(CSV_FILE, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(dict(r))
    return rows


def run_verify() -> Dict[str, Any]:
    rows = _load_csv_rows()
    conn = db_module.connect_db()
    db_module.apply_migrations(conn)

    report: Dict[str, Any] = {
        "schema_version": db_module.current_schema_version(conn),
        "started_at": _utc_now_iso(),
        "finished_at": None,
        "counts": {},
        "printer_counts": [],
        "most_recent": {},
    }

    csv_count = len(rows)
    db_count = int(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
    report["counts"]["csv_jobs"] = csv_count
    report["counts"]["db_jobs"] = db_count

    def _sum_cost(rows_list):
        total = 0.0
        for r in rows_list:
            try:
                total += float(r.get("total_cost") or 0.0)
            except Exception:
                continue
        return total

    report["counts"]["csv_total_cost"] = _sum_cost(rows)
    report["counts"]["db_total_cost"] = float(
        conn.execute("SELECT COALESCE(SUM(total_cost), 0) FROM jobs").fetchone()[0]
    )

    csv_printer_counts: Dict[str, int] = {}
    for r in rows:
        pname = str(r.get("printer") or "").strip()
        if pname:
            csv_printer_counts[pname] = csv_printer_counts.get(pname, 0) + 1

    db_printer_counts: Dict[str, int] = {}
    for row in conn.execute(
        "SELECT p.name AS printer, COUNT(*) AS cnt FROM jobs j JOIN printers p ON j.printer_id = p.id GROUP BY p.name"
    ):
        db_printer_counts[str(row["printer"])] = int(row["cnt"])

    for pname, cnt in sorted(csv_printer_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
        report["printer_counts"].append(
            {
                "printer": pname,
                "csv": cnt,
                "db": db_printer_counts.get(pname, 0),
            }
        )

    csv_times = []
    for r in rows:
        ts = _parse_timestamp(r.get("timestamp"))
        if ts is not None:
            csv_times.append(ts)
    report["most_recent"]["csv"] = max(csv_times) if csv_times else 0.0
    row = conn.execute("SELECT MAX(ended_at) AS ended_at FROM jobs").fetchone()
    report["most_recent"]["db"] = row["ended_at"] if row else None

    report["finished_at"] = _utc_now_iso()

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(VERIFY_REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    return report


def render_verify_summary(report: Dict[str, Any]) -> str:
    counts = report.get("counts", {})
    lines = [
        "Verify complete.",
        f"  CSV jobs: {counts.get('csv_jobs', 0)}",
        f"  DB jobs: {counts.get('db_jobs', 0)}",
        f"  CSV total cost: {counts.get('csv_total_cost', 0):.2f}",
        f"  DB total cost: {counts.get('db_total_cost', 0):.2f}",
        f"  Report: {VERIFY_REPORT_PATH}",
    ]
    return "\n".join(lines)
