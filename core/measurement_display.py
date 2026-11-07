"""Read-only consumption presentation; pricing units are deliberately separate.

Never consult a current printer/profile to invent a historical conversion. Callers
may supply a documented per-item factor or an already derived view measurement.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

UNSET = object()


def normalize_preference(value):
    return value if value in ("weight", "length") else "weight"


def _number(value, *, positive=False):
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or (positive and result <= 0):
            return None
        # Bounded rendering even for damaged historical data with huge exponents.
        if result.adjusted() > 30 or result.adjusted() < -100:
            return None
        return result
    except (InvalidOperation, ValueError, TypeError):
        return None


def _first(row, keys):
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _formatted(value):
    with localcontext() as context:
        context.prec = 40
        return format(value.quantize(Decimal(".01"), rounding=ROUND_HALF_UP), ".2f")


def filament_display(row=None, *, grams=UNSET, meters=UNSET, factor=UNSET,
                     source=None, kind=None, preference="weight"):
    """One primary consumption value plus optional unrounded audit metadata.

    Explicit ``None`` means unknown, not zero. Saved estimated grams remain a
    saved measurement even when a current profile's conversion has changed.
    This function does not mutate its input or any stored record.
    """
    row = row or {}
    raw_grams = (_first(row, ("_weight_grams", "filament_grams", "grams", "filament_g", "est_filament_g"))
                 if grams is UNSET else grams)
    raw_meters = (_first(row, ("_length_meters", "filament_meters", "meters"))
                  if meters is UNSET else meters)
    if meters is UNSET and raw_meters is None:
        mm = _number(_first(row, ("filament_mm",)))
        raw_meters = None if mm is None else mm / 1000
    raw_factor = (_first(row, ("_weight_factor", "grams_per_meter"))
                  if factor is UNSET else factor)
    g, m, f = _number(raw_grams), _number(raw_meters), _number(raw_factor, positive=True)
    provenance = source or row.get("_weight_source") or row.get("conversion_source")
    weight_kind = kind or row.get("filament_weight_kind") or row.get("weight_kind")
    if f is None and factor is UNSET:
        density = _number(row.get("filament_density"), positive=True)
        diameter = _number(row.get("filament_diameter"), positive=True)
        if density is not None and diameter is not None:
            from core.filament import grams_per_meter_from_geometry
            f = _number(grams_per_meter_from_geometry(density, diameter), positive=True)
            provenance = provenance or "Recorded density and input filament diameter"
    if g is None and m is not None:
        if f is not None:
            g = m * f
            weight_kind = weight_kind or "estimated"
            provenance = provenance or "Recorded job grams per meter"
        elif m == 0:
            g = Decimal(0)
            provenance = provenance or "Zero recorded length"
    if m is None and g is not None:
        if f is not None:
            m = g / f
        elif g == 0:
            m = Decimal(0)
    if g is not None:
        weight_kind = weight_kind or "recorded"
        provenance = provenance or ("Saved estimated weight" if weight_kind == "estimated" else "Recorded weight")
    pref = normalize_preference(preference)
    value = g if pref == "weight" else m
    unit = "kg" if pref == "weight" and g is not None and g >= 1000 else "g" if pref == "weight" else "m"
    primary = value / 1000 if unit == "kg" else value
    return {
        "text": "Unknown " + pref if value is None else _formatted(primary) + " " + unit,
        "grams": g, "meters": m, "factor": f, "source": provenance or "Conversion unavailable",
        "kind": weight_kind or "unknown", "preference": pref, "unknown": value is None,
    }


def filament_total(rows, *, preference="weight"):
    """Known subtotal with coverage; never apply one factor to mixed materials."""
    measures = [filament_display(row) for row in rows]
    total = {}
    for dimension in ("grams", "meters"):
        known = [item[dimension] for item in measures if item[dimension] is not None]
        total[dimension] = sum(known, Decimal(0)) if known or not measures else None
        total["unknown_" + dimension] = len(measures) - len(known)
    result = filament_display(grams=total["grams"], meters=total["meters"], preference=preference,
                              source="Per-item known subtotal", kind="mixed")
    result.update(unknown_weights=total["unknown_grams"], unknown_lengths=total["unknown_meters"], count=len(measures))
    result["unknown_count"] = result["unknown_weights" if result["preference"] == "weight" else "unknown_lengths"]
    return result


def project_item_measurement(grams, notes="", project_factor=None, *, estimated=False):
    """Use an item's reviewed conversion before an explicit project material.

    Old/manual grams without either association have unknown length. Free-form
    notes are never a license to infer an arbitrary conversion factor.
    """
    import json
    factor = None
    source = "Recorded manual weight" if not estimated else "Planned weight estimate"
    try:
        metadata = json.loads(notes) if isinstance(notes, str) else {}
        estimate = metadata.get("estimate", {}) if isinstance(metadata, dict) else {}
        if isinstance(estimate, dict):
            factor = _number(estimate.get("grams_per_meter"), positive=True)
        if factor is not None:
            source = "Reviewed estimate conversion"
    except (ValueError, TypeError):
        pass
    if factor is None:
        factor = _number(project_factor, positive=True)
        if factor is not None:
            source += "; project material conversion (current snapshot)"
    return {"filament_grams": grams, "grams_per_meter": factor,
            "filament_weight_kind": "estimated" if estimated else "recorded",
            "conversion_source": source}
