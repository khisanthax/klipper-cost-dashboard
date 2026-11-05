"""SQL order metadata and project-backed financial views."""

from contextlib import closing
from datetime import date
from decimal import Decimal, InvalidOperation
import json

from core import db, projects
from core.filament import quantize


AMOUNTS = {
    "estimated_cost": "Estimated total cost",
    "quote": "Quoted price",
    "selling_price": "Selling price",
    "revenue": "Recorded revenue",
    "expenses": "Additional expenses",
}
STATUSES = ("draft", "active", "complete")
PRIORITIES = ("low", "normal", "high")
COMPONENTS = ("time_cost", "material_cost", "adjustment", "total_cost")


def _decimal(raw):
    if raw is None or raw == "":
        return None
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _fields(form):
    result = {}
    for field, limit in (("customer", 200), ("description", 1000), ("notes", 4000)):
        value = str(form.get(field, "")).strip()
        if len(value) > limit:
            raise ValueError(f"{field.title()} must be {limit} characters or fewer.")
        result[field] = value
    result["status"] = form.get("status")
    result["priority"] = form.get("priority")
    if result["status"] not in STATUSES or result["priority"] not in PRIORITIES:
        raise ValueError("Choose a valid order status and priority.")
    due = str(form.get("due_date", "")).strip()
    try:
        result["due_date"] = date.fromisoformat(due).isoformat() if due else None
    except ValueError as exc:
        raise ValueError("Enter a valid due date.") from exc
    for field, label in AMOUNTS.items():
        raw = str(form.get(field, "")).strip()
        value = _decimal(raw)
        if raw and (value is None or not Decimal(0) <= value <= Decimal("1000000000")
                    or value * 100 != (value * 100).to_integral_value()):
            raise ValueError(f"{label} must be a nonnegative USD amount with at most two decimal places.")
        result[field + "_cents"] = int(value * 100) if value is not None else None
    return result


def list_orders():
    with closing(db.connect_db()) as conn:
        db.apply_migrations(conn)
        return [dict(row) for row in conn.execute("""
            SELECT COALESCE(NULLIF(p.project_uid,''),CAST(p.id AS TEXT)) AS project_uid,
                   p.name, o.status, o.customer, o.priority, o.due_date, o.completed_at,
                   ((p.project_uid IS NULL OR p.project_uid='') AND EXISTS(
                       SELECT 1 FROM projects named WHERE named.project_uid=CAST(p.id AS TEXT)
                   )) AS identity_conflict
            FROM business_orders o JOIN projects p ON p.id = o.project_id
            ORDER BY CASE o.status WHEN 'active' THEN 0 WHEN 'draft' THEN 1 ELSE 2 END,
                     o.due_date IS NULL, o.due_date, p.name
        """)]


def _project_id(conn, project_uid):
    row = conn.execute(
        """SELECT id FROM projects
           WHERE COALESCE(NULLIF(project_uid,''),CAST(id AS TEXT))=?
           ORDER BY CASE WHEN project_uid=? THEN 0 ELSE 1 END LIMIT 1""",
        (project_uid, project_uid),
    ).fetchone()
    return row[0] if row is not None else None


def get_order(project_uid):
    with closing(db.connect_db()) as conn:
        db.apply_migrations(conn)
        row = conn.execute("SELECT * FROM business_orders WHERE project_id=?",
                           (_project_id(conn, project_uid),)).fetchone()
    if row is None:
        return None
    result = dict(row)
    for field in AMOUNTS:
        cents = result.pop(field + "_cents")
        result[field] = Decimal(cents) / 100 if cents is not None else None
    return result


def save_order(project_uid, form):
    delete = form.get("action") == "delete"
    values = {} if delete else _fields(form)
    with closing(db.connect_db()) as conn:
        db.apply_migrations(conn)
        conn.execute("BEGIN IMMEDIATE")
        project_id = _project_id(conn, project_uid)
        if project_id is None:
            raise ValueError("Project no longer exists.")
        current = conn.execute("SELECT * FROM business_orders WHERE project_id = ?", (project_id,)).fetchone()
        if (current["updated_at"] if current else "") != form.get("revision", ""):
            raise ValueError("This order changed. Reload the page before editing.")
        if delete:
            if current is None or current["status"] != "draft":
                raise ValueError("Only a draft order can be deleted.")
            conn.execute("DELETE FROM business_orders WHERE project_id = ?", (project_id,))
        else:
            now = db._utc_now_iso()
            completed_at = None
            if values["status"] == "complete":
                completed_at = (current["completed_at"] if current else None) or now
            values.update(project_id=project_id, completed_at=completed_at, updated_at=now)
            columns = ", ".join(values)
            placeholders = ", ".join("?" for _ in values)
            updates = ", ".join(f"{column} = excluded.{column}" for column in values if column != "project_id")
            conn.execute(f"""
                INSERT INTO business_orders ({columns}) VALUES ({placeholders})
                ON CONFLICT(project_id) DO UPDATE SET {updates}
            """, tuple(values.values()))
        conn.commit()


def financial_view(project, order):
    """Use stored prices or an explicit project-only snapshot for tracked jobs."""
    with closing(db.connect_db()) as conn:
        db.apply_migrations(conn)
        tracked = [dict(row) for row in conn.execute("""
            SELECT j.job_uid, j.filename, j.status, j.time_cost, j.material_cost, j.total_cost, pp.calculation_json
            FROM project_assignments a JOIN jobs j ON j.job_uid = a.job_uid
            JOIN projects p ON p.id = a.project_id
            LEFT JOIN project_job_pricing pp ON pp.project_id = a.project_id AND pp.job_uid = j.job_uid
            WHERE a.project_id = ? ORDER BY j.ended_at DESC, j.id DESC
        """, (_project_id(conn, project.id),))]
    actual = dict.fromkeys(COMPONENTS, Decimal(0))
    missing_totals = 0
    manual = projects.load_manual_jobs().get(project.id, [])
    inputs = []
    for row in tracked:
        overlay = row.pop("calculation_json")
        prices = json.loads(overlay)["after"] if overlay else row
        time_cost, material, total = (_decimal(prices.get(key)) for key in ("time_cost", "material_cost", "total_cost"))
        adjustment = total - time_cost - material if None not in (time_cost, material, total) else None
        row.update(time_cost=time_cost, material_cost=material, adjustment=adjustment, total_cost=total)
        row["price_source"] = "Project-adjusted snapshot" if overlay else "Saved historical cost"
        inputs.append(row)
    for job in manual:
        components = projects.compute_manual_job_cost_components(job, project=project)
        inputs.append({key: _decimal(components[key]) for key in COMPONENTS})
    for values in inputs:
        if values["total_cost"] is None:
            missing_totals += 1
        for key in COMPONENTS:
            value = values[key]
            actual[key] = actual[key] + value if actual[key] is not None and value is not None else None

    # The order view reconciles display cents while retaining exact saved prices.
    actual = {key: quantize(value) if value is not None else None for key, value in actual.items()}
    if all(value is not None for value in actual.values()):
        actual["adjustment"] = actual["total_cost"] - actual["time_cost"] - actual["material_cost"]
    projected = projects.compute_project_projection(projects.load_plans().get(project.id, []), project=project)
    expenses = order.get("expenses")
    cost = actual["total_cost"] + expenses if actual["total_cost"] is not None and expenses is not None else None
    revenue = order.get("revenue")
    profit = revenue - cost if revenue is not None and cost is not None else None
    estimate = order.get("estimated_cost")
    return {
        "actual": actual, "tracked": tracked, "manual_count": len(manual), "missing_totals": missing_totals,
        "planned_cost": quantize(projected["total_cost"]), "planned_count": int(projected["count"]),
        "actual_cost": cost, "profit": profit,
        "margin": 100 * profit / revenue if profit is not None and revenue > 0 else None,
        "estimate_variance": cost - estimate if cost is not None and estimate is not None else None,
    }
