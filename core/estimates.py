"""Read-only pre-print estimates using the established planned-item pricing rules."""

from .filament import project_price_from_form, quantize
from .gcode_metadata import extract_gcode_metadata_stream
from .numeric import finite_float, optional_finite_float
from .projects import compute_planned_item_cost_components_from_pricing


def estimate_print(values, upload=None):
    for field in ("duration_seconds", "filament_grams", "hourly_rate", "filament_price_per_kg", "grams_per_meter"):
        if isinstance(values.get(field), bool):
            raise ValueError("Measurements and rates must be numeric values.")
    metadata = None
    if upload is not None and upload.filename:
        if not upload.filename.lower().endswith(".gcode"):
            raise ValueError("Select a .gcode file.")
        metadata = extract_gcode_metadata_stream(upload.stream)
    duration = optional_finite_float(values.get("duration_seconds"), label="Duration", positive=True)
    if duration is None and metadata:
        duration = metadata.time_s
    if duration is None or not 0 < duration <= 31 * 86400:
        raise ValueError("Enter a positive estimate of at most 31 days or upload G-code with a duration.")
    grams = optional_finite_float(values.get("filament_grams"), label="Filament grams", nonnegative=True)
    weight_source = "Entered grams" if grams is not None else "Unknown"
    if grams is None and metadata and metadata.filament_g is not None:
        grams, weight_source = metadata.filament_g, "Slicer grams"
    rate = finite_float(values.get("hourly_rate"), label="Machine price per hour", nonnegative=True)
    per_kg, profile = project_price_from_form({
        "project_filament_profile_id": values.get("filament_profile_id"),
        "pricing_amount": values.get("filament_price_per_kg"),
        "pricing_mode": "per_kg",
    })
    if per_kg is None:
        raise ValueError("Select a material profile or enter its price per kilogram.")
    per_kg = finite_float(per_kg, label="Filament price per kilogram", nonnegative=True)
    factor = optional_finite_float(values.get("grams_per_meter"), label="Grams per meter", positive=True)
    if factor is None and profile:
        factor = optional_finite_float(profile.get("grams_per_meter"), label="Profile grams per meter", positive=True)
    if grams is None and metadata and metadata.filament_mm is not None and factor is not None:
        grams = metadata.filament_mm / 1000 * factor
        weight_source = "Slicer millimeters converted using grams per meter"
    if (grams is not None and not 0 <= grams <= 1_000_000) or max(rate, per_kg) > 1_000_000:
        raise ValueError("Filament weight or price exceeds the estimate limit.")
    costs = compute_planned_item_cost_components_from_pricing(
        est_time_s=duration, est_filament_g=grams, hourly_rate=rate, filament_cost_per_kg=per_kg, labor_only=False,
    )
    if grams is None:
        costs.update(material_cost=None, total_cost=None)
    displayed_time = quantize(costs["time_cost"])
    displayed_material = quantize(costs["material_cost"]) if grams is not None else None
    return {
        **costs, "duration_seconds": duration, "filament_grams": grams, "weight_source": weight_source,
        "filament_price_per_kg": per_kg, "hourly_rate": rate,
        "slicer": metadata.slicer if metadata else None, "kind": "estimate",
        "display_time": str(displayed_time),
        "display_material": str(displayed_material) if displayed_material is not None else None,
        "display_total": str(displayed_time + displayed_material) if displayed_material is not None else None,
    }
