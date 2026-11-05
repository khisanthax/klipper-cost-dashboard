"""Physical spool balances and an append-only record of measured changes."""
from contextlib import closing
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from uuid import UUID

from core import db
from core.numeric import optional_finite_float
from core.sql_only import is_sql_only


class InventoryConflict(ValueError):
    """A replay or job conflicts with an existing stock event."""


def _connect():
    if not is_sql_only():
        raise ValueError("Filament inventory requires SQL-only storage.")
    return db.connect_db()


def _identifier(value):
    try:
        return UUID(str(value)).hex
    except ValueError as error:
        raise ValueError("Invalid inventory operation identifier.") from error


def _amount(value, scale, label, *, signed=False):
    raw = str(value).strip()
    if len(raw) > 80:
        raise ValueError(f"{label} exceeds the supported amount or precision.")
    try:
        number = Decimal(raw)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{label} must be a number.") from error
    if not number.is_finite() or number.copy_abs() > Decimal(1_000_000_000) / scale:
        raise ValueError(f"{label} exceeds the supported amount or precision.")
    if number and number.copy_abs() < Decimal(1) / scale:
        raise ValueError(f"{label} exceeds the supported amount or precision.")
    with localcontext() as context:
        context.prec = 90
        number *= scale
    if number != number.to_integral_value():
        raise ValueError(f"{label} exceeds the supported amount or precision.")
    if not signed and number < 0:
        raise ValueError(f"{label} must be non-negative.")
    return int(number)


def _text(value, label, *, required=False):
    text = str(value or "").strip()
    if len(text) > 160 or (required and not text):
        raise ValueError(f"{label} must contain {'1–160' if required else 'up to 160'} characters.")
    return text


def create_spool(operation_uid, *, manufacturer, material, color, net_grams, remaining_grams,
                 low_stock_grams, purchase_price=None, purchased_on=None, grams_per_meter=None):
    uid = _identifier(operation_uid)
    manufacturer = _text(manufacturer, "Manufacturer")
    material = _text(material, "Material", required=True)
    color = _text(color, "Color")
    net = _amount(net_grams, 1000, "Net weight")
    remaining = _amount(remaining_grams, 1000, "Opening balance")
    low = _amount(low_stock_grams, 1000, "Low-stock threshold")
    if not net or remaining > net or low > net:
        raise ValueError("Opening balance and low-stock threshold must fit within the net weight.")
    purchase = (
        _amount(purchase_price, 100, "Purchase price")
        if purchase_price is not None and str(purchase_price).strip() else None
    )
    purchase_date = date.fromisoformat(str(purchased_on)).isoformat() if purchased_on else None
    conversion = optional_finite_float(grams_per_meter, label="Verified grams per meter", positive=True)
    payload = (manufacturer, material, color, net, low, purchase, purchase_date, conversion)
    with closing(_connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            """SELECT s.manufacturer, s.material, s.color, s.net_mg, s.low_stock_mg,
                      s.purchase_cents, s.purchased_on, s.grams_per_meter, e.delta_mg
               FROM filament_spools s JOIN filament_stock_events e ON e.spool_uid=s.spool_uid
               WHERE s.spool_uid=? AND e.kind='opening'""",
            (uid,),
        ).fetchone()
        if existing is not None:
            if tuple(existing) != (*payload, remaining):
                raise InventoryConflict("This spool operation already recorded different details.")
            return uid, False
        if conn.execute("SELECT 1 FROM filament_stock_events WHERE event_uid=?", (uid,)).fetchone():
            raise InventoryConflict("This operation identifier belongs to another stock event.")
        now = db._utc_now_iso()
        conn.execute(
            """INSERT INTO filament_spools (
                spool_uid, manufacturer, material, color, net_mg, low_stock_mg, purchase_cents,
                purchased_on, grams_per_meter, remaining_mg, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (uid, *payload, remaining, now),
        )
        conn.execute(
            """INSERT INTO filament_stock_events
               (event_uid, spool_uid, kind, delta_mg, reason, recorded_at)
               VALUES (?, ?, 'opening', ?, 'Entered opening balance', ?)""",
            (uid, uid, remaining, now),
        )
    return uid, True


def assign_spool(spool_uid, printer_id):
    uid = _identifier(spool_uid)
    with closing(_connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        _active_spool(conn, uid)
        if printer_id and not conn.execute("SELECT 1 FROM printers WHERE id=?", (printer_id,)).fetchone():
            raise ValueError("Select a registered printer.")
        result = conn.execute("UPDATE filament_spools SET printer_id=? WHERE spool_uid=?", (printer_id or None, uid))
        if result.rowcount != 1:
            raise ValueError("Spool not found.")


def record_consumption(spool_uid, operation_uid, job_uid, grams, *, verified):
    if verified is not True:
        raise ValueError("Confirm a measured amount before deducting stock.")
    measured = _amount(grams, 1000, "Measured consumption")
    if measured <= 0:
        raise ValueError("Measured consumption must be greater than zero.")
    return _record_event(
        spool_uid, operation_uid, "verified_consumption", -measured,
        "Operator-confirmed measured consumption", _text(job_uid, "Job UID", required=True),
    )


def adjust_stock(spool_uid, operation_uid, delta_grams, reason):
    delta = _amount(delta_grams, 1000, "Adjustment", signed=True)
    if not delta:
        raise ValueError("An adjustment must change the balance.")
    return _record_event(
        spool_uid, operation_uid, "adjustment", delta, _text(reason, "Reason", required=True), None,
    )


def _active_spool(conn, spool_uid):
    row = conn.execute(
        """SELECT a.archived_at FROM filament_spools s
           LEFT JOIN filament_spool_archives a USING (spool_uid) WHERE s.spool_uid=?""", (spool_uid,),
    ).fetchone()
    if row is None:
        raise ValueError("Spool not found.")
    if row["archived_at"]:
        raise ValueError("Reactivate this archived spool before changing its stock or assignment.")


def _change_balance(conn, spool_uid, delta):
    result = conn.execute(
        """UPDATE filament_spools SET remaining_mg=remaining_mg+?
           WHERE spool_uid=? AND remaining_mg+? BETWEEN 0 AND net_mg""",
        (delta, spool_uid, delta),
    )
    if result.rowcount != 1:
        raise ValueError("The change would put the spool balance outside 0–net weight.")


def correct_consumption(spool_uid, operation_uid, original_event_uid, grams, *, expected_grams, reason):
    """Replace the measured total by appending a linked, balance-safe correction."""
    spool_uid, operation_uid, original = map(_identifier, (spool_uid, operation_uid, original_event_uid))
    measured = _amount(grams, 1000, "Corrected consumption")
    expected = _amount(expected_grams, 1000, "Previous measured consumption")
    reason = _text(reason, "Correction reason", required=True)
    with closing(_connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        replay = conn.execute(
            """SELECT e.spool_uid, c.original_event_uid, c.previous_measured_mg, c.measured_mg, e.reason
               FROM filament_stock_events e LEFT JOIN filament_consumption_corrections c USING (event_uid)
               WHERE e.event_uid=?""", (operation_uid,),
        ).fetchone()
        if replay is not None:
            if tuple(replay) != (spool_uid, original, expected, measured, reason):
                raise InventoryConflict("This correction operation already recorded different details.")
            return False
        _active_spool(conn, spool_uid)
        initial = conn.execute(
            """SELECT delta_mg FROM filament_stock_events
               WHERE event_uid=? AND spool_uid=? AND kind='verified_consumption'""",
            (original, spool_uid),
        ).fetchone()
        if initial is None:
            raise ValueError("Select a verified consumption record from this spool.")
        corrections = conn.execute(
            """SELECT COALESCE(SUM(e.delta_mg), 0) FROM filament_consumption_corrections c
               JOIN filament_stock_events e USING (event_uid) WHERE c.original_event_uid=?""", (original,),
        ).fetchone()[0]
        current = -initial["delta_mg"] - corrections
        if current != expected:
            raise InventoryConflict("Consumption changed since this form was opened. Refresh and review it again.")
        if current == measured:
            return False
        delta = current - measured
        _change_balance(conn, spool_uid, delta)
        conn.execute(
            """INSERT INTO filament_stock_events (event_uid, spool_uid, kind, delta_mg, reason, recorded_at)
               VALUES (?, ?, 'adjustment', ?, ?, ?)""",
            (operation_uid, spool_uid, delta, reason, db._utc_now_iso()),
        )
        conn.execute(
            "INSERT INTO filament_consumption_corrections VALUES (?, ?, ?, ?)",
            (operation_uid, original, current, measured),
        )
    return True


def record_estimate(spool_uid, operation_uid, job_uid, grams, source):
    """Retain an explicitly labelled estimate; never update physical balance."""
    spool_uid, operation_uid = map(_identifier, (spool_uid, operation_uid))
    job_uid = _text(job_uid, "Job UID", required=True)
    estimated = _amount(grams, 1000, "Estimated consumption")
    source = _text(source, "Estimate source", required=True)
    with closing(_connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        replay = conn.execute(
            "SELECT spool_uid, job_uid, estimated_mg, source FROM filament_consumption_estimates WHERE operation_uid=?",
            (operation_uid,),
        ).fetchone()
        if replay is not None:
            if tuple(replay) != (spool_uid, job_uid, estimated, source):
                raise InventoryConflict("This estimate operation already recorded different details.")
            return False
        _active_spool(conn, spool_uid)
        job = conn.execute("SELECT filename FROM jobs WHERE job_uid=?", (job_uid,)).fetchone()
        if job is None:
            raise ValueError("Select an existing history or tracked job.")
        conn.execute(
            "INSERT INTO filament_consumption_estimates VALUES (?, ?, ?, ?, ?, ?, ?)",
            (operation_uid, spool_uid, job_uid, job["filename"], estimated, source, db._utc_now_iso()),
        )
    return True


def set_spool_archived(spool_uid, operation_uid, *, archived, reason):
    spool_uid, operation_uid = map(_identifier, (spool_uid, operation_uid))
    if type(archived) is not bool:
        raise ValueError("Choose archive or reactivate explicitly.")
    reason = _text(reason, "Archive or reactivation reason", required=True)
    with closing(_connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        replay = conn.execute(
            "SELECT spool_uid, archived, reason FROM filament_spool_lifecycle_events WHERE operation_uid=?", (operation_uid,),
        ).fetchone()
        if replay is not None:
            if tuple(replay) != (spool_uid, int(archived), reason):
                raise InventoryConflict("This archive operation already recorded different details.")
            return False
        spool = conn.execute(
            """SELECT a.archived_at FROM filament_spools s
               LEFT JOIN filament_spool_archives a USING (spool_uid) WHERE s.spool_uid=?""", (spool_uid,),
        ).fetchone()
        if spool is None:
            raise ValueError("Spool not found.")
        if bool(spool["archived_at"]) == archived:
            return False
        now = db._utc_now_iso()
        conn.execute(
            """INSERT INTO filament_spool_archives VALUES (?, ?, ?)
               ON CONFLICT(spool_uid) DO UPDATE SET archived_at=excluded.archived_at, reason=excluded.reason""",
            (spool_uid, now if archived else None, reason),
        )
        conn.execute("INSERT INTO filament_spool_lifecycle_events VALUES (?, ?, ?, ?, ?)",
                     (operation_uid, spool_uid, int(archived), reason, now))
    return True


def _record_event(spool_uid, operation_uid, kind, delta, reason, job_uid):
    spool_uid = _identifier(spool_uid)
    operation_uid = _identifier(operation_uid)
    with closing(_connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        expected = (spool_uid, kind, delta, job_uid, reason)
        event = conn.execute(
            "SELECT spool_uid, kind, delta_mg, job_uid, reason FROM filament_stock_events WHERE event_uid=?",
            (operation_uid,),
        ).fetchone()
        if event is not None:
            if tuple(event) != expected:
                raise InventoryConflict("This operation already recorded different details.")
            return False
        _active_spool(conn, spool_uid)
        filename = None
        if job_uid is not None:
            previous = conn.execute(
                "SELECT delta_mg FROM filament_stock_events WHERE spool_uid=? AND job_uid=?",
                (spool_uid, job_uid),
            ).fetchone()
            if previous is not None:
                if previous["delta_mg"] != delta:
                    raise InventoryConflict("This job already used this spool. Use its linked consumption correction to change it.")
                return False
            job = conn.execute("SELECT filename, status FROM jobs WHERE job_uid=?", (job_uid,)).fetchone()
            if job is None or job["status"].lower() not in {"completed", "failed", "canceled", "cancelled"}:
                raise ValueError("Select a completed, failed or canceled history job.")
            filename = job["filename"]
        _change_balance(conn, spool_uid, delta)
        conn.execute(
            """INSERT INTO filament_stock_events (
                 event_uid, spool_uid, kind, delta_mg, job_uid, job_filename, reason, recorded_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (operation_uid, spool_uid, kind, delta, job_uid, filename, reason, db._utc_now_iso()),
        )
    return True


def inventory_view():
    with closing(_connect()) as conn:
        spools = [dict(row) for row in conn.execute(
            """SELECT s.*, p.name AS printer_name, a.archived_at, a.reason AS archive_reason FROM filament_spools s
               LEFT JOIN printers p ON p.id=s.printer_id LEFT JOIN filament_spool_archives a USING (spool_uid)
               ORDER BY s.created_at DESC, s.spool_uid"""
        )]
        printers = [dict(row) for row in conn.execute("SELECT id, name FROM printers ORDER BY name")]
        events = [dict(row) for row in conn.execute(
            """SELECT e.*, s.material, s.color, c.original_event_uid,
                      original.job_uid AS corrected_job_uid, original.job_filename AS corrected_job_filename
               FROM filament_stock_events e JOIN filament_spools s USING (spool_uid)
               LEFT JOIN filament_consumption_corrections c USING (event_uid)
               LEFT JOIN filament_stock_events original ON original.event_uid=c.original_event_uid
               ORDER BY e.recorded_at DESC, e.rowid DESC LIMIT 100"""
        )]
        for spool in spools:
            spool["consumptions"] = [dict(row) for row in conn.execute(
                """SELECT original.event_uid, original.job_uid, original.job_filename,
                          -original.delta_mg - COALESCE(SUM(e.delta_mg), 0) AS measured_mg
                   FROM filament_stock_events original
                   LEFT JOIN filament_consumption_corrections c ON c.original_event_uid=original.event_uid
                   LEFT JOIN filament_stock_events e ON e.event_uid=c.event_uid
                   WHERE original.spool_uid=? AND original.kind='verified_consumption'
                   GROUP BY original.event_uid ORDER BY original.recorded_at DESC LIMIT 100""", (spool["spool_uid"],),
            )]
            spool["estimates"] = [dict(row) for row in conn.execute(
                """SELECT estimate.* FROM filament_consumption_estimates estimate
                   WHERE spool_uid=? AND NOT EXISTS (
                       SELECT 1 FROM filament_consumption_estimates newer
                       WHERE newer.spool_uid=estimate.spool_uid AND newer.job_uid=estimate.job_uid
                       AND newer.rowid > estimate.rowid)
                   ORDER BY recorded_at DESC, rowid DESC LIMIT 100""", (spool["spool_uid"],),
            )]
        jobs = [dict(row) for row in conn.execute(
            """SELECT j.job_uid, j.filename, j.status, p.name AS printer_name
               FROM jobs j JOIN printers p ON p.id=j.printer_id
               WHERE lower(j.status) IN ('completed','failed','canceled','cancelled')
               ORDER BY j.ended_at DESC, j.id DESC LIMIT 100"""
        )]
    return spools, printers, events, jobs
