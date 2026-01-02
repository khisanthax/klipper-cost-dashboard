"""
Parity checks for reports aggregates (CSV vs SQL).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

from core.config import DATA_DIR
from core.reports_repo import get_reports_data_range
from core.storage import ts_to_local_dt

REPORT_PATH = os.path.join(DATA_DIR, "reports_parity_report.json")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_range(range_str: str) -> int:
    raw = str(range_str or "").strip().lower()
    if not raw:
        return 30
    if raw.endswith("d"):
        try:
            return max(1, int(raw[:-1]))
        except Exception:
            return 30
    try:
        return max(1, int(raw))
    except Exception:
        return 30


def _now_local() -> datetime:
    return ts_to_local_dt(datetime.now().timestamp())


def _float(val: Any) -> float:
    try:
        if val is None:
            return 0.0
        return float(val)
    except Exception:
        return 0.0


def _diff(a: float, b: float, tol: float = 0.01) -> bool:
    return abs(a - b) > tol


def _index_by_key(rows: List[dict], key: str) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for r in rows or []:
        k = str(r.get(key) or "").strip()
        if not k:
            continue
        out[k] = r
    return out


def run_parity(*, range_str: str = "30d") -> Dict[str, Any]:
    days = _parse_range(range_str)
    now = _now_local()
    start_dt = now - timedelta(days=days)

    csv_data = get_reports_data_range(
        start_dt=start_dt,
        end_dt=now,
        range_label=f"Last {days} days",
        quick_range=f"last{days}",
        backend_override="csv",
    )
    sql_data = get_reports_data_range(
        start_dt=start_dt,
        end_dt=now,
        range_label=f"Last {days} days",
        quick_range=f"last{days}",
        backend_override="sql",
    )

    mismatches: List[dict] = []

    csv_summary = csv_data.get("summary", {})
    sql_summary = sql_data.get("summary", {})
    for key in ("total_prints", "total_hours", "total_meters", "total_cost"):
        csv_val = _float(csv_summary.get(key))
        sql_val = _float(sql_summary.get(key))
        if key == "total_prints":
            if int(csv_val) != int(sql_val):
                mismatches.append({"field": key, "csv": int(csv_val), "sql": int(sql_val)})
        elif _diff(csv_val, sql_val):
            mismatches.append({"field": key, "csv": csv_val, "sql": sql_val})

    csv_printers = _index_by_key(csv_data.get("top_printers", []), "name")
    sql_printers = _index_by_key(sql_data.get("top_printers", []), "name")
    for name, row in csv_printers.items():
        other = sql_printers.get(name)
        if not other:
            mismatches.append({"field": "printer_missing_sql", "printer": name})
            continue
        if int(row.get("count", 0) or 0) != int(other.get("count", 0) or 0):
            mismatches.append(
                {
                    "field": "printer_count",
                    "printer": name,
                    "csv": int(row.get("count", 0) or 0),
                    "sql": int(other.get("count", 0) or 0),
                }
            )
        if _diff(_float(row.get("cost")), _float(other.get("cost"))):
            mismatches.append(
                {
                    "field": "printer_cost",
                    "printer": name,
                    "csv": _float(row.get("cost")),
                    "sql": _float(other.get("cost")),
                }
            )

    csv_months = _index_by_key(csv_data.get("monthly_breakdown", []), "label")
    sql_months = _index_by_key(sql_data.get("monthly_breakdown", []), "label")
    for label, row in csv_months.items():
        other = sql_months.get(label)
        if not other:
            mismatches.append({"field": "month_missing_sql", "label": label})
            continue
        if int(row.get("count", 0) or 0) != int(other.get("count", 0) or 0):
            mismatches.append(
                {
                    "field": "month_count",
                    "label": label,
                    "csv": int(row.get("count", 0) or 0),
                    "sql": int(other.get("count", 0) or 0),
                }
            )
        if _diff(_float(row.get("cost")), _float(other.get("cost"))):
            mismatches.append(
                {
                    "field": "month_cost",
                    "label": label,
                    "csv": _float(row.get("cost")),
                    "sql": _float(other.get("cost")),
                }
            )

    report = {
        "range": range_str,
        "started_at": _utc_now_iso(),
        "finished_at": _utc_now_iso(),
        "csv_summary": csv_summary,
        "sql_summary": sql_summary,
        "mismatches": mismatches,
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    return report


def render_parity_summary(report: Dict[str, Any]) -> str:
    mismatches = report.get("mismatches", [])
    lines = [
        "Reports parity check (CSV vs SQL)",
        f"  Range: {report.get('range', '')}",
        f"  Mismatches: {len(mismatches)}",
        f"  Report: {REPORT_PATH}",
    ]
    if mismatches:
        lines.append("  Top mismatches:")
        for item in mismatches[:10]:
            lines.append(f"    - {item}")
    return "\n".join(lines)
