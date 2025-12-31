"""
Parity check between CSV history and SQL history.
"""
from __future__ import annotations

from typing import Dict, List

from core.history_repo import HistoryQuery, list_history_rows_sql, list_history_rows_csv


def run_parity(limit: int = 200) -> Dict[str, object]:
    query = HistoryQuery()

    csv_result = list_history_rows_csv(query, page=1, per_page=max(limit, 1), error=None)
    sql_result = list_history_rows_sql(query, page=1, per_page=max(limit, 1), error=None)

    csv_rows = csv_result.rows_page[:limit]
    sql_rows = sql_result.rows_page[:limit]

    mismatches = []
    fields = ["job_uid", "printer", "filename", "timestamp", "status", "total_cost"]

    for idx in range(max(len(csv_rows), len(sql_rows))):
        csv_row = csv_rows[idx] if idx < len(csv_rows) else None
        sql_row = sql_rows[idx] if idx < len(sql_rows) else None
        if csv_row is None or sql_row is None:
            mismatches.append({"index": idx, "reason": "missing_row"})
            continue
        diffs = {}
        for field in fields:
            csv_val = str(csv_row.get(field) or "")
            sql_val = str(sql_row.get(field) or "")
            if csv_val != sql_val:
                diffs[field] = {"csv": csv_val, "sql": sql_val}
        if diffs:
            mismatches.append({"index": idx, "diffs": diffs})

    return {
        "limit": limit,
        "csv_count": len(csv_rows),
        "sql_count": len(sql_rows),
        "mismatches": mismatches,
    }


def render_parity_summary(report: Dict[str, object]) -> str:
    mismatches = report.get("mismatches", [])
    lines = [
        "History parity check complete.",
        f"  Rows compared: {report.get('csv_count', 0)} (csv) vs {report.get('sql_count', 0)} (sql)",
        f"  Mismatches: {len(mismatches)}",
    ]
    if mismatches:
        lines.append("  First mismatch index: %s" % (mismatches[0].get("index") if mismatches else "n/a"))
    return "\n".join(lines)
