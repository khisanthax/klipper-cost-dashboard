"""
Cost calculations and printer management for Print Cost Dashboard.
"""
import os
import csv
from core.config import DEFAULT_PRICING, CSV_FILE, SETTINGS_FILE, HEADERS, DISPLAY_FILE
from core.storage import (
    load_settings,
    save_settings,
    load_rows_raw,
    load_state,
    save_state,
    load_display_settings,
    save_hidden_printers,
)
from core.sql_only import is_sql_only
from core import profiles
from core import rates


class Cfg:
    """Pricing configuration wrapper."""
    def __init__(self, d):
        self.rate_per_hour = d.get("rate_per_hour", DEFAULT_PRICING["rate_per_hour"])
        self.filament_mode = d.get("filament_mode", DEFAULT_PRICING["filament_mode"])
        self.filament_rate = d.get("filament_rate", DEFAULT_PRICING["filament_rate"])
        self.grams_per_meter = d.get("grams_per_meter", DEFAULT_PRICING["grams_per_meter"])


def _is_sql_only() -> bool:
    return is_sql_only()


def _get_printer_settings(printer_name: str) -> dict:
    settings = load_settings(SETTINGS_FILE)
    printer_settings = settings.get(printer_name, {}) if isinstance(settings, dict) else {}
    return printer_settings if isinstance(printer_settings, dict) else {}


def get_pricing_for_printer_raw(printer_name: str) -> dict:
    """Get pricing configuration for a printer as a dictionary."""
    base = dict(DEFAULT_PRICING)
    base.update(_get_printer_settings(printer_name))
    return base


def get_pricing_for_printer(printer_name: str) -> Cfg:
    """Get pricing configuration for a printer as a Cfg object."""
    return Cfg(get_pricing_for_printer_raw(printer_name))


def get_effective_rate_per_hour(printer_name: str) -> float:
    """
    Resolve the hourly rate for a printer, honoring an active rate profile if set.
    """
    printer_settings = _get_printer_settings(printer_name)
    active_rate_profile_id = printer_settings.get("active_rate_profile_id")

    if active_rate_profile_id:
        profile = rates.get_rate_profile(active_rate_profile_id)
        if profile:
            try:
                return float(profile.get("rate_per_hour", DEFAULT_PRICING["rate_per_hour"]))
            except (TypeError, ValueError):
                pass

    # Fallback to printer's own rate
    cfg = get_pricing_for_printer(printer_name)
    try:
        return float(cfg.rate_per_hour)
    except (TypeError, ValueError):
        return DEFAULT_PRICING["rate_per_hour"]


def _get_effective_filament_pricing(printer_name: str) -> dict:
    """
    Get effective filament pricing for a printer.
    
    Prefers active profile data if available and complete.
    Falls back to printer-level settings otherwise.
    
    Returns dict with: filament_mode, filament_rate, grams_per_meter
    """
    # Try to get active profile for this printer
    profile_id = profiles.get_printer_mapping(printer_name)
    
    if profile_id:
        profile_data = profiles.get_profile(profile_id)
        
        # Check if profile exists and has complete filament pricing data
        if profile_data:
            has_mode = "filament_mode" in profile_data
            has_rate = "filament_rate" in profile_data
            has_grams = "grams_per_meter" in profile_data
            
            if has_mode and has_rate and has_grams:
                # Profile has complete data, use it
                return {
                    "filament_mode": profile_data["filament_mode"],
                    "filament_rate": profile_data["filament_rate"],
                    "grams_per_meter": profile_data["grams_per_meter"],
                }
    
    # No profile or incomplete data, fall back to printer settings
    printer_cfg = get_pricing_for_printer_raw(printer_name)
    return {
        "filament_mode": printer_cfg["filament_mode"],
        "filament_rate": printer_cfg["filament_rate"],
        "grams_per_meter": printer_cfg["grams_per_meter"],
    }


def _include_paused_time_for_printer(printer_name: str) -> bool:
    """
    Resolve whether a printer should include paused time in hourly billing.

    Precedence:
    1) Per-printer override (settings.json) when enabled.
    2) Global default (display.json).
    """
    try:
        printer_settings = _get_printer_settings(printer_name)
        if printer_settings.get("pause_include_paused_time_override_enabled", False):
            return bool(printer_settings.get("pause_include_paused_time_override_value", False))
        # Backwards compatibility: old "exclude paused" semantics.
        if printer_settings.get("pause_exclude_paused_time_override_enabled", False):
            return not bool(printer_settings.get("pause_exclude_paused_time_override_value", False))
    except Exception:
        pass

    try:
        display = load_display_settings(DISPLAY_FILE, HEADERS)
        if "pause_include_paused_time_default" in display:
            return bool(display.get("pause_include_paused_time_default", False))
        if "pause_exclude_paused_time_default" in display:
            return not bool(display.get("pause_exclude_paused_time_default", False))
        return False
    except Exception:
        return False


def compute_costs(
    printer_name: str, duration_seconds: float, filament_mm: float, paused_seconds_total: float = 0.0
) -> dict:
    """
    Compute costs for a print job.
    
    Returns a dictionary with:
    - duration_hours, filament_meters
    - rate_per_hour, filament_mode, filament_rate, grams_per_meter
    - time_cost, material_cost, total_cost
    - filament_profile_id, filament_material

    Billing rule:
    - Any non-zero duration is billed with a minimum of 1.0 hour.
      (duration_hours is still the actual elapsed time for reporting.)
    """
    # Time cost uses effective rate (rate profile overrides printer base)
    rate_per_hour = float(get_effective_rate_per_hour(printer_name))
    
    # Material cost prefers profile data if available
    filament_pricing = _get_effective_filament_pricing(printer_name)
    filament_mode = filament_pricing["filament_mode"]
    filament_rate = float(filament_pricing["filament_rate"])
    grams_per_meter = float(filament_pricing["grams_per_meter"])
    
    # Get profile info for tracking
    profile_id = profiles.get_printer_mapping(printer_name)
    profile_material = ""
    if profile_id:
        profile_data = profiles.get_profile(profile_id)
        if profile_data:
            profile_material = profile_data.get("material", "")
    else:
        profile_id = ""

    # Actual usage metrics (for reporting)
    duration_hours = duration_seconds / 3600.0
    filament_meters = filament_mm / 1000.0
    grams = filament_meters * grams_per_meter

    # --- Billing rule: minimum 1 hour for any non-zero billable time ---
    billable_seconds = float(duration_seconds)
    # Default behavior: EXCLUDE paused time from hourly billing unless explicitly enabled.
    if not _include_paused_time_for_printer(printer_name):
        try:
            billable_seconds = max(0.0, float(duration_seconds) - float(paused_seconds_total))
        except Exception:
            billable_seconds = max(0.0, float(duration_seconds))

    if billable_seconds > 0:
        billable_hours = max(1.0, billable_seconds / 3600.0)
    else:
        billable_hours = 0.0

    time_cost = billable_hours * rate_per_hour

    if filament_mode == "per_meter":
        material_cost = filament_meters * filament_rate
    elif filament_mode == "per_gram":
        material_cost = grams * filament_rate
    elif filament_mode == "per_kg":
        kg = grams / 1000.0
        material_cost = kg * filament_rate
    else:
        material_cost = 0.0

    total_cost = time_cost + material_cost

    return {
        "duration_hours": duration_hours,
        "filament_meters": filament_meters,
        "rate_per_hour": rate_per_hour,
        "filament_mode": filament_mode,
        "filament_rate": filament_rate,
        "grams_per_meter": grams_per_meter,
        "time_cost": time_cost,
        "material_cost": material_cost,
        "total_cost": total_cost,
        "filament_profile_id": profile_id,
        "filament_material": profile_material,
    }


def compute_costs_with_overrides(
    printer_name: str,
    duration_seconds: float,
    filament_mm: float,
    paused_seconds_total: float = 0.0,
    *,
    filament_profile_id: str | None = None,
    rate_profile_id: str | None = None,
    rate_per_hour_override: float | None = None,
    filament_rate_per_meter_override: float | None = None,
) -> dict:
    """
    Compute costs for a print job, optionally overriding the filament profile and/or rate profile.

    This does NOT modify printer defaults or active profile mappings. It is intended for one-off
    recalculation scenarios (e.g. Recalculate Center plan application).

    When no overrides are provided, the behavior matches compute_costs() (same pricing rules).
    """
    # --- Rate override (optional) ---
    rate_per_hour = None
    if rate_per_hour_override is not None:
        try:
            rate_per_hour = float(rate_per_hour_override)
        except (TypeError, ValueError):
            rate_per_hour = None
    if rate_profile_id:
        if not _is_sql_only():
            profile = rates.get_rate_profile(rate_profile_id)
            if profile:
                try:
                    rate_per_hour = float(profile.get("rate_per_hour", DEFAULT_PRICING["rate_per_hour"]))
                except (TypeError, ValueError):
                    rate_per_hour = None
    if rate_per_hour is None:
        rate_per_hour = float(get_effective_rate_per_hour(printer_name))

    # --- Filament override (optional) ---
    profile_id = ""
    profile_material = ""
    filament_mode = None
    filament_rate = None
    grams_per_meter = None

    if filament_rate_per_meter_override is not None:
        try:
            filament_mode = "per_meter"
            filament_rate = float(filament_rate_per_meter_override)
        except (TypeError, ValueError):
            filament_mode = None
            filament_rate = None

    if filament_profile_id:
        if not _is_sql_only():
            profile_data = profiles.get_profile(filament_profile_id)
            if profile_data:
                has_mode = "filament_mode" in profile_data
                has_rate = "filament_rate" in profile_data
                has_grams = "grams_per_meter" in profile_data
                if has_mode and has_rate and has_grams:
                    profile_id = filament_profile_id
                    profile_material = profile_data.get("material", "") or ""
                    filament_mode = profile_data["filament_mode"]
                    filament_rate = profile_data["filament_rate"]
                    grams_per_meter = profile_data["grams_per_meter"]

    if filament_mode is None or filament_rate is None or grams_per_meter is None:
        filament_pricing = _get_effective_filament_pricing(printer_name)
        filament_mode = filament_pricing["filament_mode"]
        filament_rate = filament_pricing["filament_rate"]
        grams_per_meter = filament_pricing["grams_per_meter"]

        # Tracking info from the printer's active mapping (unless a manual per-meter override is used)
        if filament_rate_per_meter_override is None:
            profile_id = profiles.get_printer_mapping(printer_name) or ""
            if profile_id:
                profile_data = profiles.get_profile(profile_id)
                if profile_data:
                    profile_material = profile_data.get("material", "") or ""
            else:
                profile_id = ""

    filament_rate = float(filament_rate)
    grams_per_meter = float(grams_per_meter)

    # Actual usage metrics (for reporting)
    duration_hours = duration_seconds / 3600.0
    filament_meters = filament_mm / 1000.0
    grams = filament_meters * grams_per_meter

    # Billing rule: minimum 1 hour for any non-zero billable time
    billable_seconds = float(duration_seconds)
    # Default behavior: EXCLUDE paused time from hourly billing unless explicitly enabled.
    if not _include_paused_time_for_printer(printer_name):
        try:
            billable_seconds = max(0.0, float(duration_seconds) - float(paused_seconds_total))
        except Exception:
            billable_seconds = max(0.0, float(duration_seconds))

    if billable_seconds > 0:
        billable_hours = max(1.0, billable_seconds / 3600.0)
    else:
        billable_hours = 0.0

    time_cost = billable_hours * rate_per_hour

    if filament_mode == "per_meter":
        material_cost = filament_meters * filament_rate
    elif filament_mode == "per_gram":
        material_cost = grams * filament_rate
    elif filament_mode == "per_kg":
        kg = grams / 1000.0
        material_cost = kg * filament_rate
    else:
        material_cost = 0.0

    total_cost = time_cost + material_cost

    return {
        "duration_hours": duration_hours,
        "filament_meters": filament_meters,
        "rate_per_hour": rate_per_hour,
        "filament_mode": filament_mode,
        "filament_rate": filament_rate,
        "grams_per_meter": grams_per_meter,
        "time_cost": time_cost,
        "material_cost": material_cost,
        "total_cost": total_cost,
        "filament_profile_id": profile_id,
        "filament_material": profile_material,
    }


def compute_live_time_cost(printer_name, elapsed_seconds):
    """
    Compute time cost for an in-progress job.

    NOTE: This uses actual elapsed time (no 1-hour minimum) so that
    "Cost So Far" grows smoothly during the print. The final logged
    job and estimated totals still apply the 1-hour minimum via
    compute_costs / compute_estimated_final_cost.
    
    Args:
        printer_name: Name of printer
        elapsed_seconds: Time elapsed so far
    
    Returns:
        float: Time cost so far
    """
    rate_per_hour = get_effective_rate_per_hour(printer_name)
    elapsed_hours = elapsed_seconds / 3600.0
    return elapsed_hours * rate_per_hour


def compute_estimated_final_cost(printer_name, estimated_duration, estimated_filament_mm, profile_id=None):
    """
    Estimate final cost based on estimated values.

    Applies the same 1-hour minimum billing rule as compute_costs.
    
    Args:
        printer_name: Name of printer
        estimated_duration: Estimated duration in seconds
        estimated_filament_mm: Estimated filament in mm
        profile_id: Optional profile ID (currently unused; pricing is
                    resolved via printer + active profile mappings).
    
    Returns:
        dict: {
            "estimated_time_cost": float,
            "estimated_material_cost": float,
            "estimated_total_cost": float
        }
    """
    # Reuse existing compute_costs logic (includes 1-hour minimum)
    result = compute_costs(printer_name, estimated_duration, estimated_filament_mm)
    
    return {
        "estimated_time_cost": result["time_cost"],
        "estimated_material_cost": result["material_cost"],
        "estimated_total_cost": result["total_cost"]
    }


def get_known_printers():
    """
    Get canonical printer list from persisted registries, excluding hidden printers.

    Hidden printers are a soft-delete mechanism used to keep configuration UIs clean
    without deleting historical CSV rows.
    """
    from core.printers import get_canonical_printer_names

    def _norm(name) -> str:
        return str(name or "").strip()

    hidden = {_norm(p) for p in load_display_settings(DISPLAY_FILE, HEADERS).get("hidden_printers", [])}
    printers = get_canonical_printer_names(include_hidden=True)
    return sorted([p for p in printers if p and p not in hidden])


def get_configured_printers():
    """Printers explicitly configured in settings.json (excluding hidden)."""
    def _norm(name) -> str:
        return str(name or "").strip()

    settings = load_settings(SETTINGS_FILE)
    hidden = {_norm(p) for p in load_display_settings(DISPLAY_FILE, HEADERS).get("hidden_printers", [])}
    return sorted([p for p in settings.keys() if _norm(p) and _norm(p) not in hidden])


def get_discovered_printers():
    """
    Printers found only via CSV history (not present in settings.json), excluding hidden.
    Used for optional display in Settings.
    """
    def _norm(name) -> str:
        return str(name or "").strip()

    settings = load_settings(SETTINGS_FILE)
    configured = {_norm(p) for p in settings.keys() if _norm(p)}
    hidden = {_norm(p) for p in load_display_settings(DISPLAY_FILE, HEADERS).get("hidden_printers", [])}
    if _is_sql_only():
        from core.printers import get_canonical_printer_names

        discovered = {_norm(p) for p in get_canonical_printer_names(include_hidden=True) if _norm(p)}
        discovered = discovered - configured - hidden
        return sorted(discovered)

    discovered = set()
    if os.path.exists(CSV_FILE):
        try:
            with open(CSV_FILE, newline="") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    p = _norm(r.get("printer"))
                    if p:
                        discovered.add(p)
        except Exception:
            pass
    discovered = discovered - configured - hidden
    return sorted(discovered)


def hide_printer(printer_name: str) -> None:
    """
    Soft-delete: hide a printer from configuration views without deleting CSV rows.
    """
    printer_name = str(printer_name or "").strip()
    if not printer_name:
        return
    display = load_display_settings(DISPLAY_FILE, HEADERS)
    hidden = display.get("hidden_printers", [])
    if not isinstance(hidden, list):
        hidden = []
    hidden_set = {str(p).strip() for p in hidden if str(p).strip()}
    hidden_set.add(printer_name)
    save_hidden_printers(DISPLAY_FILE, HEADERS, sorted(hidden_set))


def rename_printer(old_name, new_name, update_csv=True):
    """Rename a printer in settings, client registry, and optionally CSV."""
    from core.config import DATA_DIR
    
    # Update settings
    settings = load_settings(SETTINGS_FILE)
    if old_name in settings:
        settings[new_name] = settings.pop(old_name)
        save_settings(SETTINGS_FILE, DATA_DIR, settings)
    
    # Update client registry
    state_file = os.path.join(DATA_DIR, "install_state.json")
    registry = load_state(state_file, "clients", [])
    changed = False
    for entry in registry:
        if entry.get("printer_name") == old_name:
            entry["printer_name"] = new_name
            changed = True
    if changed:
        save_state(state_file, DATA_DIR, "clients", registry)
    
    # Update CSV if requested
    if update_csv and os.path.exists(CSV_FILE):
        rows, _ = load_rows_raw(CSV_FILE)
        for row in rows:
            if row.get("printer") == old_name:
                row["printer"] = new_name
        with open(CSV_FILE, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=HEADERS)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)


def merge_printers(names_to_merge, merged_name):
    """Merge multiple printers into one in settings, registry, and CSV."""
    from core.config import DATA_DIR
    
    # (1) Combine settings (last one wins)
    settings = load_settings(SETTINGS_FILE)
    for name in names_to_merge:
        if name in settings:
            settings[merged_name] = settings.pop(name)
    save_settings(SETTINGS_FILE, DATA_DIR, settings)
    
    # (2) Update registry
    state_file = os.path.join(DATA_DIR, "install_state.json")
    registry = load_state(state_file, "clients", [])
    changed = False
    for entry in registry:
        if entry.get("printer_name") in names_to_merge:
            entry["printer_name"] = merged_name
            changed = True
    if changed:
        save_state(state_file, DATA_DIR, "clients", registry)
    
    # (3) Update CSV
    if os.path.exists(CSV_FILE):
        rows, _ = load_rows_raw(CSV_FILE)
        for row in rows:
            if row.get("printer") in names_to_merge:
                row["printer"] = merged_name
        with open(CSV_FILE, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=HEADERS)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)


def list_printers():
    """Return a sorted list of printer names from settings.json."""
    settings = load_settings(SETTINGS_FILE)
    return sorted(settings.keys())


def delete_printer(printer_name, delete_csv=False):
    """
    Remove a printer from settings, client registry, and optionally CSV.
    Intended for cleaning up bogus printers like '*.gcode'.
    """
    from core.config import DATA_DIR

    target = str(printer_name or "").strip()
    if not target:
        return

    # Remove from settings.json (match by normalized name).
    settings = load_settings(SETTINGS_FILE)
    removed_any = False
    for key in list(settings.keys()):
        if str(key).strip() == target:
            settings.pop(key, None)
            removed_any = True
    if removed_any:
        save_settings(SETTINGS_FILE, DATA_DIR, settings)

    # Remove from client registry in install_state.json
    state_file = os.path.join(DATA_DIR, "install_state.json")
    registry = load_state(state_file, "clients", [])
    new_registry = [entry for entry in registry if str(entry.get("printer_name") or "").strip() != target]
    if len(new_registry) != len(registry):
        save_state(state_file, DATA_DIR, "clients", new_registry)

    # Optionally remove rows from CSV
    if delete_csv and os.path.exists(CSV_FILE):
        rows, _ = load_rows_raw(CSV_FILE)
        rows = [row for row in rows if str(row.get("printer") or "").strip() != target]
        with open(CSV_FILE, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=HEADERS)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)


def clean_gcode_printers(delete_csv=False):
    """
    Delete any printers in settings.json that look like '.gcode' filenames.
    If delete_csv is True, also remove their rows from print_costs.csv.
    """
    settings = load_settings(SETTINGS_FILE)
    names = list(settings.keys())
    gcode_like = [
        name for name in names
        if ".gcode" in name.lower() or name.lower().endswith(".gco")
    ]

    if not gcode_like:
        print("No .gcode-like printers found in settings.json.")
        return

    print("Removing these .gcode-like printers:")
    for name in gcode_like:
        print(f"  - {name}")
        delete_printer(name, delete_csv=delete_csv)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Printer management utilities.")
    parser.add_argument("--list", action="store_true",
                        help="List all printers from settings.json.")
    parser.add_argument("--rename", nargs=2, metavar=("OLD", "NEW"),
                        help="Rename a printer (updates settings, clients, and CSV).")
    parser.add_argument("--delete", metavar="NAME",
                        help="Delete a printer from settings and clients, optionally CSV.")
    parser.add_argument("--clean-gcode", action="store_true",
                        help="Delete printers whose names look like '.gcode' filenames.")
    parser.add_argument("--delete-csv", action="store_true",
                        help="When deleting/cleaning, also remove matching rows from CSV.")

    args = parser.parse_args()

    if args.list:
        printers = list_printers()
        if not printers:
            print("No printers found in settings.json.")
        else:
            print("Printers in settings.json:")
            for name in printers:
                print(f"  - {name}")

    if args.rename:
        old_name, new_name = args.rename
        update_csv = not args.delete_csv  # reuse flag: if delete-csv is set, skip CSV update here
        rename_printer(old_name, new_name, update_csv=update_csv)
        print(f"Renamed printer '{old_name}' -> '{new_name}' (CSV updated={update_csv}).")

    if args.delete:
        delete_printer(args.delete, delete_csv=args.delete_csv)
        print(f"Deleted printer '{args.delete}' (CSV rows deleted={args.delete_csv}).")

    if args.clean_gcode:
        clean_gcode_printers(delete_csv=args.delete_csv)
