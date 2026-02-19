# --------------------------------------------------------------------------------------
# File: installer_macro.py
# Description: Macro integration helpers for Print Cost Dashboard installer.
#  - Finds and patches END_PRINT-style macros to send cost data (KCD block).
#  - Finds and patches START_PRINT-style macros to call KCD_JOB_START.
#  - Finds and patches CANCEL_PRINT-style macros to call KCD_JOB_CANCEL.
#  - Finds and patches PAUSE/RESUME macros to call KCD_JOB_PAUSE / KCD_JOB_RESUME.
#  - STRICT mode for end macros: insert KCD block before heater/fan/stepper shutdown.
#  - CLEAN mode: remove only KCD marker blocks we previously inserted.
#  - Optional user hook: "### KCD_INSERT_CODE ###" inside a macro to control insertion.
# --------------------------------------------------------------------------------------

import re
import os
import glob


# ======================================================================
# Utility: KCD markers + generic hook
# ======================================================================

KCD_INSERT_HOOK = "### KCD_INSERT_CODE ###"

KCD_START_END_MARKER = "### KCD START (END_PRINT)"
KCD_END_END_MARKER = "### KCD END (END_PRINT)"

KCD_START_START_MARKER = "### KCD START (START_PRINT)"
KCD_END_START_MARKER = "### KCD END (START_PRINT)"

KCD_START_CANCEL_MARKER = "### KCD START (CANCEL_PRINT)"
KCD_END_CANCEL_MARKER = "### KCD END (CANCEL_PRINT)"

KCD_START_PAUSE_MARKER = "### KCD START (PAUSE)"
KCD_END_PAUSE_MARKER = "### KCD END (PAUSE)"

KCD_START_RESUME_MARKER = "### KCD START (RESUME)"
KCD_END_RESUME_MARKER = "### KCD END (RESUME)"

KCD_START_FILAMENT_CHANGE_MARKER = "### KCD START (FILAMENT_CHANGE)"
KCD_END_FILAMENT_CHANGE_MARKER = "### KCD END (FILAMENT_CHANGE)"


def _clean_marked_block(block_lines, start_marker, end_marker):
    """
    Remove any sections in block_lines delimited by start_marker / end_marker.
    Returns a new list with those sections stripped.
    """
    cleaned = []
    inside = False
    for line in block_lines:
        stripped = line.strip()
        if stripped.startswith(start_marker):
            inside = True
            continue
        if inside:
            if stripped.startswith(end_marker):
                inside = False
            continue
        cleaned.append(line)
    return cleaned


def _find_macro_block(lines, macro_name):
    """
    Given a list of lines and a macro name, locate the macro block.
    Returns (start_index, end_index) where end_index is exclusive, or (-1, -1) if not found.
    """
    start_index = -1
    macro_header = f"[gcode_macro {macro_name}]".lower()

    for i, line in enumerate(lines):
        if line.strip().lower() == macro_header:
            start_index = i
            break

    if start_index == -1:
        return -1, -1

    end_index = len(lines)
    for i in range(start_index + 1, len(lines)):
        if lines[i].strip().startswith("["):
            end_index = i
            break

    return start_index, end_index


def _detect_indent(macro_block):
    """
    Determine indentation for lines under 'gcode:' in the given macro_block.
    Default is 4 spaces if not inferable.
    """
    indent = "    "
    gcode_start = -1
    for i, line in enumerate(macro_block):
        if line.strip().startswith("gcode:"):
            gcode_start = i
            break

    if gcode_start != -1:
        for line in macro_block[gcode_start + 1:]:
            if line.strip():
                leading = len(line) - len(line.lstrip())
                if leading > 0:
                    indent = " " * leading
                break

    return indent


def _apply_insert_hook_or_auto(lines, start_index, end_index, patterns_for_auto):
    """
    Shared logic to determine insertion index inside a macro:

    - If KCD_INSERT_HOOK is found, remove that line and use its position.
    - Otherwise, for END macros we look for shutdown patterns (TURN_OFF_HEATERS, M84, etc.).
    - For START macros, the caller can pass patterns_for_auto=None and handle fallback.

    Returns (insertion_index, new_end_index, used_hook).
    """
    used_hook = False
    # Work on a slice for readability, but remember indices are in the outer list.
    macro_block = lines[start_index:end_index]

    # 1) Look for the generic hook
    hook_index = None
    for i, line in enumerate(macro_block):
        if KCD_INSERT_HOOK in line:
            hook_index = i
            break

    if hook_index is not None:
        used_hook = True
        # Global index
        insertion_index = start_index + hook_index
        # Remove the hook line itself
        del lines[insertion_index]
        end_index -= 1
        return insertion_index, end_index, used_hook

    # 2) Auto mode with patterns (used for END macros)
    if patterns_for_auto:
        insertion_index = None
        macro_block = lines[start_index:end_index]  # refresh reference
        for i, line in enumerate(macro_block):
            stripped = line.strip()
            for pattern in patterns_for_auto:
                if re.search(pattern, stripped, re.IGNORECASE):
                    insertion_index = start_index + i
                    break
            if insertion_index is not None:
                break

        if insertion_index is None:
            insertion_index = end_index

        return insertion_index, end_index, used_hook

    # 3) If no patterns and no hook, caller will decide (e.g., for START macros)
    return None, end_index, used_hook


# ======================================================================
# END macro discovery
# ======================================================================

def find_macros_in_dir(config_dir):
    """
    Scans all .cfg files in the directory for macros that look like print end macros.
    Returns a list of (filename, macro_name, line_number) tuples.
    """
    if not os.path.exists(config_dir):
        return []

    macros = []
    # Regex for [gcode_macro NAME]
    # Case insensitive match for PRINT_END, END_PRINT, etc.
    target_names = ["PRINT_END", "END_PRINT", "PRINT_END_MACRO", "END_PRINT_MACRO"]

    cfg_files = glob.glob(os.path.join(config_dir, "*.cfg"))

    for path in cfg_files:
        filename = os.path.basename(path)
        try:
            with open(path, "r") as f:
                lines = f.readlines()

            for i, line in enumerate(lines):
                line_stripped = line.strip()
                if line_stripped.lower().startswith("[gcode_macro"):
                    # Extract name: [gcode_macro MY_MACRO] -> MY_MACRO
                    match = re.search(r"\[gcode_macro\s+([^\]]+)\]", line_stripped, re.IGNORECASE)
                    if match:
                        name = match.group(1).strip()
                        upper_name = name.upper()
                        # Check if it's one of our targets or similar
                        if any(t in upper_name for t in target_names):
                            macros.append((filename, name, i + 1))  # 1-based line number
        except Exception as e:
            print(f"Error reading {filename}: {e}")

    return macros


# ======================================================================
# END macro KCD insertion (STRICT + CLEAN + HOOK)
# ======================================================================

HEATER_OFF_PATTERNS = [
    r"TURN_OFF_HEATERS",
    r"_HEATERS_OFF",
    r"M104\s+S0",
    r"M140\s+S0",
    # SET_HEATER_TEMPERATURE ... TARGET=0 (OFF)
    r"SET_HEATER_TEMPERATURE.*TARGET\s*=\s*0",
]

FAN_OFF_PATTERNS = [
    r"_ALL_FAN_OFF",
    r"M107",
    r"M106\s+S0",
]

STEPPER_OFF_PATTERNS = [
    r"M84",
    r"SET_STEPPER_ENABLE.*ENABLE\s*=\s*0",
]

ALL_OFF_PATTERNS = HEATER_OFF_PATTERNS + FAN_OFF_PATTERNS + STEPPER_OFF_PATTERNS


def insert_run_shell_command(macro_name, path, printer_name):
    """
    STRICT + CLEAN + HOOK mode insertion for END_PRINT-style macro.

    - CLEAN: Remove any existing KCD block we previously inserted, delimited by:
        ### KCD START (END_PRINT) ...
        ### KCD END (END_PRINT) ###
    - HOOK: If the user placed '### KCD_INSERT_CODE ###' inside the macro, we insert
      the END-print KCD block exactly at that location (and remove the hook line).
    - STRICT: If no hook, insert the KCD block immediately BEFORE the first heater/fan/stepper
      shutdown command (TURN_OFF_HEATERS, M104 S0, M140 S0, _ALL_FAN_OFF, M84, etc.).
      If none found, append near the end of the macro.

    Note: `printer_name` is kept for compatibility but the inserted Jinja uses the
    baked value from `printer["gcode_macro _KCD_VARS"].printer_name` (set by the installer).
    """

    if not os.path.exists(path):
        return False, "File not found"

    try:
        with open(path, "r") as f:
            lines = f.readlines()
    except Exception as e:
        return False, f"Read error: {e}"

    # Locate macro block
    start_index, end_index = _find_macro_block(lines, macro_name)
    if start_index == -1:
        return False, f"Macro '{macro_name}' not found"

    macro_block = lines[start_index:end_index]

    # If a KCD END_PRINT block is already present, skip repatching.
    if any(KCD_START_END_MARKER in ln for ln in macro_block) and any(KCD_END_END_MARKER in ln for ln in macro_block):
        return True, "Command already present"

    # CLEAN mode: remove any previous KCD END_PRINT block marked with our markers
    macro_block = _clean_marked_block(macro_block, KCD_START_END_MARKER, KCD_END_END_MARKER)
    lines[start_index:end_index] = macro_block
    end_index = start_index + len(macro_block)

    # Decide insertion index using hook or STRICT patterns
    insertion_index, end_index, used_hook = _apply_insert_hook_or_auto(
        lines, start_index, end_index, ALL_OFF_PATTERNS
    )

    # Determine indentation
    macro_block = lines[start_index:end_index]
    indent = _detect_indent(macro_block)

    # Build universal KCD END block with markers
    kcd_block = [
        f"{indent}{KCD_START_END_MARKER} - DO NOT MODIFY OR DELETE THIS BLOCK ###\n",
        f"{indent}G4 P500\n",
        f"{indent}# KCD: send cost data to dashboard\n",
        f'{indent}{{% set printer_name = printer["gcode_macro _KCD_VARS"].printer_name|string %}}\n',
        f'{indent}{{% set fname = printer.print_stats.filename|default("unknown.gcode", true)|string %}}\n',
        f'{indent}{{% set fname = fname|replace(\'\\\\\', \'\\\\\\\\\')|replace(\'"\', \'\\\\"\') %}}\n',
        f"{indent}{{% set dur = printer.print_stats.print_duration|int %}}\n",
        f"{indent}{{% set filament = printer.print_stats.filament_used|default(0)|int %}}\n",
        f"{indent}{{% set params = printer_name ~ '|' ~ fname ~ '|' ~ dur ~ '|' ~ filament %}}\n",
        f'{indent}RUN_SHELL_COMMAND CMD=send_print_cost PARAMS="{{params}}"\n',
        f"{indent}{KCD_END_END_MARKER} ###\n",
        "\n",
    ]

    # If insertion_index is None (should not happen for END macros), fall back to end_index
    if insertion_index is None:
        insertion_index = end_index

    # Insert new block
    lines[insertion_index:insertion_index] = kcd_block

    # Write back
    try:
        with open(path, "w") as f:
            f.writelines(lines)
        return True, "Success"
    except Exception as e:
        return False, f"Write error: {e}"


def create_default_print_end_macro(path, printer_name):
    """
    Appends a default PRINT_END macro to the file.

    Note: This is only used when we truly do not find any END_PRINT-style macro.
    It uses the same universal KCD block and a minimal shutdown sequence.
    """
    content = f"""

[gcode_macro PRINT_END]
description: End of print macro (auto-generated)
gcode:
    {KCD_START_END_MARKER} - DO NOT MODIFY OR DELETE THIS BLOCK ###
    G4 P500

    # KCD: send cost data to dashboard
    {{% set printer_name = printer["gcode_macro _KCD_VARS"].printer_name|string %}}
    {{% set fname = printer.print_stats.filename|default("unknown.gcode", true)|string %}}
    {{% set fname = fname|replace('\\\\', '\\\\\\\\')|replace('\"', '\\\\\"') %}}
    {{% set dur = printer.print_stats.print_duration|int %}}
    {{% set filament = printer.print_stats.filament_used|default(0)|int %}}
    {{% set params = printer_name ~ '|' ~ fname ~ '|' ~ dur ~ '|' ~ filament %}}
    RUN_SHELL_COMMAND CMD=send_print_cost PARAMS="{{params}}"
    {KCD_END_END_MARKER} ###

    TURN_OFF_HEATERS
    M84
"""
    try:
        with open(path, "a") as f:
            f.write(content)
        return True, "Created new PRINT_END macro."
    except Exception as e:
        return False, f"Failed to append macro: {e}"


def prompt_macro_insertion(printer_name, config_dir, default_macro=None, default_file=None):
    """
    Interactive wizard to find and patch the print end macro.
    Returns (macro_name, target_file) if patched or already present, else (None, None).
    """
    print("\n=== Automatic Macro Integration (END_PRINT) ===")
    print("Scanning for print end macros in all .cfg files...")

    # Try fast-path if a default macro/file is provided
    if default_macro and default_file:
        full_path = os.path.join(config_dir, default_file)
        success, msg = insert_run_shell_command(default_macro, full_path, printer_name)
        if success:
            if msg == "Command already present":
                print("  - KCD block already present; skipping.")
            else:
                print(f"  - Patched {default_macro} in {default_file}.")
            return default_macro, default_file
        else:
            print(f"  - Default macro patch failed ({msg}), falling back to manual selection.")

    macros = find_macros_in_dir(config_dir)

    target_macro = None
    target_file = None

    if macros:
        print(f"Found {len(macros)} potential macro(s):")
        for i, (fname, name, line) in enumerate(macros):
            print(f"  {i+1}) {name} in {fname} (line {line})")
        print("  0) None of these / Enter manually")

        choice = input("Select macro to patch [1] (or 's' to skip): ").strip()
        if choice.lower() == 's':
            print("Skipping end macro integration.")
            return None, None

        if not choice:
            choice = "1"

        try:
            idx = int(choice)
            if 1 <= idx <= len(macros):
                target_file = macros[idx-1][0]
                target_macro = macros[idx-1][1]
            else:
                target_macro = None
        except ValueError:
            target_macro = None
    else:
        print("No obvious 'PRINT_END' or 'END_PRINT' macros found.")

    if not target_macro:
        print("\nOptions:")
        print("  1) Enter macro name manually (if it exists)")
        print("  2) Create a new default [gcode_macro PRINT_END] in printer.cfg")
        print("  3) Skip")

        ans = input("Select option [2]: ").strip()
        if ans == "1":
            target_macro = input("Enter macro name: ").strip()
            target_file = "printer.cfg"  # Default to printer.cfg if manual
        elif ans == "3" or ans.lower() == "s":
            print("Skipping end macro integration.")
            return None, None
        else:
            # Default: Create new
            printer_cfg = os.path.join(config_dir, "printer.cfg")
            print(f"Creating new PRINT_END macro in {printer_cfg}...")
            success, msg = create_default_print_end_macro(printer_cfg, printer_name)
            if success:
                print(f"  - {msg}")
            else:
                print(f"  - Failed: {msg}")
            return "PRINT_END", "printer.cfg"

    # If we are here, we have a target macro and file
    full_path = os.path.join(config_dir, target_file)
    print(f"Attempting to patch macro '{target_macro}' in {target_file}...")
    success, msg = insert_run_shell_command(target_macro, full_path, printer_name)

    if success:
        if msg == "Command already present":
            print(f"  - {msg}, skipping.")
        else:
            print(f"  - Success! Inserted universal KCD END block into {target_macro}.")
    else:
        print(f"  - Failed: {msg}")
        print("  - Please add a universal KCD block manually just before heaters/steppers shut off:")
        print('    G4 P500')
        print('    # KCD: send cost data to dashboard')
        print('    {% set printer_name = printer["gcode_macro _KCD_VARS"].printer_name|string %}')
        print('    {% set fname = printer.print_stats.filename|default("unknown.gcode", true)|string %}')
        print('    {% set fname = fname|replace(\'\\\\\', \'\\\\\\\\\')|replace(\'"\', \'\\\\"\') %}')
        print('    {% set dur = printer.print_stats.print_duration|int %}')
        print('    {% set filament = printer.print_stats.filament_used|default(0)|int %}')
        print('    {% set params = printer_name ~ \'|\' ~ fname ~ \'|\' ~ dur ~ \'|\' ~ filament %}')
        print('    RUN_SHELL_COMMAND CMD=send_print_cost PARAMS="{{params}}"')
        return None, None

    return target_macro, target_file


# ======================================================================
# Start macro helpers (HOOK + CLEAN markers)
# ======================================================================

def find_start_macros_in_dir(config_dir):
    """
    Scans all .cfg files in the directory for macros that look like start macros.
    Returns a list of (filename, macro_name, line_number) tuples.
    """
    if not os.path.exists(config_dir):
        return []

    macros = []
    target_names = ["PRINT_START", "START_PRINT", "PRINT_BEGIN", "START_JOB"]
    cfg_files = glob.glob(os.path.join(config_dir, "*.cfg"))

    for path in cfg_files:
        filename = os.path.basename(path)
        try:
            with open(path, "r") as f:
                lines = f.readlines()
            for i, line in enumerate(lines):
                line_stripped = line.strip()
                if line_stripped.startswith("[gcode_macro"):
                    match = re.search(r"\[gcode_macro\s+([^\]]+)\]", line_stripped, re.IGNORECASE)
                    if match:
                        name = match.group(1).strip()
                        upper_name = name.upper()
                        if any(t in upper_name for t in target_names):
                            macros.append((filename, name, i + 1))
        except Exception as e:
            print(f"Error reading {filename}: {e}")
    return macros


def find_cancel_macros_in_dir(config_dir):
    """
    Scans all .cfg files in the directory for macros that look like print cancel macros.
    Returns a list of (filename, macro_name, line_number) tuples.
    """
    if not os.path.exists(config_dir):
        return []

    macros = []
    target_names = ["CANCEL_PRINT", "PRINT_CANCEL", "CANCEL_JOB", "ABORT_PRINT"]
    cfg_files = glob.glob(os.path.join(config_dir, "*.cfg"))

    for path in cfg_files:
        filename = os.path.basename(path)
        try:
            with open(path, "r") as f:
                lines = f.readlines()
            for i, line in enumerate(lines):
                line_stripped = line.strip()
                if line_stripped.startswith("[gcode_macro"):
                    match = re.search(r"\[gcode_macro\s+([^\]]+)\]", line_stripped, re.IGNORECASE)
                    if match:
                        name = match.group(1).strip()
                        upper_name = name.upper()
                        if any(t in upper_name for t in target_names):
                            macros.append((filename, name, i + 1))
        except Exception as e:
            print(f"Error reading {filename}: {e}")
    return macros


def find_pause_macros_in_dir(config_dir):
    """
    Scans all .cfg files in the directory for macros that look like pause macros.
    Returns a list of (filename, macro_name, line_number) tuples.
    """
    if not os.path.exists(config_dir):
        return []

    macros = []
    target_names = {"PAUSE", "PAUSE_PRINT", "PRINT_PAUSE"}
    cfg_files = glob.glob(os.path.join(config_dir, "*.cfg"))

    for path in cfg_files:
        filename = os.path.basename(path)
        try:
            with open(path, "r") as f:
                lines = f.readlines()
            for i, line in enumerate(lines):
                line_stripped = line.strip()
                if line_stripped.startswith("[gcode_macro"):
                    match = re.search(r"\[gcode_macro\s+([^\]]+)\]", line_stripped, re.IGNORECASE)
                    if match:
                        name = match.group(1).strip()
                        if name.upper() in target_names:
                            macros.append((filename, name, i + 1))
        except Exception as e:
            print(f"Error reading {filename}: {e}")
    return macros


def find_resume_macros_in_dir(config_dir):
    """
    Scans all .cfg files in the directory for macros that look like resume macros.
    Returns a list of (filename, macro_name, line_number) tuples.
    """
    if not os.path.exists(config_dir):
        return []

    macros = []
    target_names = {"RESUME", "RESUME_PRINT", "PRINT_RESUME"}
    cfg_files = glob.glob(os.path.join(config_dir, "*.cfg"))

    for path in cfg_files:
        filename = os.path.basename(path)
        try:
            with open(path, "r") as f:
                lines = f.readlines()
            for i, line in enumerate(lines):
                line_stripped = line.strip()
                if line_stripped.startswith("[gcode_macro"):
                    match = re.search(r"\[gcode_macro\s+([^\]]+)\]", line_stripped, re.IGNORECASE)
                    if match:
                        name = match.group(1).strip()
                        if name.upper() in target_names:
                            macros.append((filename, name, i + 1))
        except Exception as e:
            print(f"Error reading {filename}: {e}")
    return macros


def find_filament_change_macros_in_dir(config_dir):
    """
    Scans all .cfg files in the directory for macros that look like filament-change macros.

    We keep this conservative because macro semantics vary widely.
    Returns a list of (filename, macro_name, line_number) tuples.
    """
    if not os.path.exists(config_dir):
        return []

    macros = []
    target_names = {"M600", "FILAMENT_CHANGE"}
    cfg_files = glob.glob(os.path.join(config_dir, "*.cfg"))

    for path in cfg_files:
        filename = os.path.basename(path)
        try:
            with open(path, "r") as f:
                lines = f.readlines()
            for i, line in enumerate(lines):
                line_stripped = line.strip()
                if line_stripped.startswith("[gcode_macro"):
                    match = re.search(r"\[gcode_macro\s+([^\]]+)\]", line_stripped, re.IGNORECASE)
                    if match:
                        name = match.group(1).strip()
                        if name.upper() in target_names:
                            macros.append((filename, name, i + 1))
        except Exception as e:
            print(f"Error reading {filename}: {e}")
    return macros


def insert_filament_change_macro_call(macro_name, path, call_line):
    """
    Insert a KCD pause call with a specific reason into a filament-change macro.

    This is intentionally separate from PAUSE macro insertion so we can keep
    marker semantics clear and avoid duplicates on re-run.
    """
    if not os.path.exists(path):
        return False, "File not found"

    try:
        with open(path, "r") as f:
            lines = f.readlines()
    except Exception as e:
        return False, f"Read error: {e}"

    start_index, end_index = _find_macro_block(lines, macro_name)
    if start_index == -1:
        return False, f"Macro '{macro_name}' not found"

    macro_block = lines[start_index:end_index]

    if any(KCD_START_FILAMENT_CHANGE_MARKER in ln for ln in macro_block) and any(
        KCD_END_FILAMENT_CHANGE_MARKER in ln for ln in macro_block
    ):
        return True, "Call already present"

    macro_block = _clean_marked_block(
        macro_block, KCD_START_FILAMENT_CHANGE_MARKER, KCD_END_FILAMENT_CHANGE_MARKER
    )
    lines[start_index:end_index] = macro_block
    end_index = start_index + len(macro_block)

    for i in range(start_index, end_index):
        if call_line in lines[i]:
            return True, "Call already present"

    macro_block = lines[start_index:end_index]
    indent = _detect_indent(macro_block)

    kcd_block = [
        f"{indent}{KCD_START_FILAMENT_CHANGE_MARKER} - DO NOT MODIFY OR DELETE THIS BLOCK ###\n",
        f"{indent}# KCD: annotate pause reason (filament change)\n",
        f"{indent}{call_line}\n",
        f"{indent}{KCD_END_FILAMENT_CHANGE_MARKER} ###\n",
        "\n",
    ]

    # Insert after gcode: if present; else create a gcode: section.
    gcode_start = -1
    for i in range(start_index, end_index):
        if lines[i].strip().startswith("gcode:"):
            gcode_start = i
            break
    if gcode_start == -1:
        gcode_line_index = start_index + 1
        lines.insert(gcode_line_index, "gcode:\n")
        lines[gcode_line_index + 1:gcode_line_index + 1] = kcd_block
    else:
        lines[gcode_start + 1:gcode_start + 1] = kcd_block

    try:
        with open(path, "w") as f:
            f.writelines(lines)
        return True, "Success"
    except Exception as e:
        return False, f"Write error: {e}"


def prompt_filament_change_macro_insertion(printer_name, config_dir):
    """
    Optional wizard: patch M600/FILAMENT_CHANGE macros to send a pause reason.

    This may result in a second pause signal if the macro also triggers PAUSE;
    the server-side pause handler is idempotent and will treat this safely.
    """
    print("\n=== Optional Filament-Change Pause Reason Integration ===")
    print("This will add: KCD_JOB_PAUSE REASON=filament_change")
    choice = input("Patch filament-change macros (M600/FILAMENT_CHANGE)? [y/N]: ").strip().lower()
    if choice not in ("y", "yes"):
        print("Skipping filament-change macro integration.")
        return None, None

    macros = find_filament_change_macros_in_dir(config_dir)
    if not macros:
        print("No obvious filament-change macros found.")
        return None, None

    print(f"Found {len(macros)} potential filament-change macro(s):")
    for i, (fname, name, line) in enumerate(macros):
        print(f"  {i+1}) {name} in {fname} (line {line})")
    print("  0) Skip")
    resp = input("Select macro to patch [1]: ").strip()
    if resp == "" :
        resp = "1"
    if resp.lower() == "s" or resp == "0":
        print("Skipping filament-change macro integration.")
        return None, None
    if not resp.isdigit():
        print("Invalid choice; skipping.")
        return None, None
    idx = int(resp)
    if idx < 1 or idx > len(macros):
        print("Invalid selection; skipping.")
        return None, None

    target_file, target_macro, _line = macros[idx - 1]
    full_path = os.path.join(config_dir, target_file)
    call_line = "KCD_JOB_PAUSE REASON=filament_change"
    print(f"Attempting to patch filament-change macro '{target_macro}' in {target_file}...")
    success, msg = insert_filament_change_macro_call(target_macro, full_path, call_line)
    if success:
        if msg == "Call already present":
            print(f"  - {msg}, skipping.")
        else:
            print("  - Success! Added filament_change pause reason block.")
        return target_macro, target_file
    print(f"  - Failed: {msg}")
    print("  - Please add the following line manually:")
    print(f"    {call_line}")
    return None, None

def insert_pause_macro_call(macro_name, path, macro_to_call):
    """
    Insert KCD_JOB_PAUSE into a PAUSE macro without breaking existing behavior.

    - CLEAN: remove previously inserted PAUSE block between PAUSE markers.
    - HOOK: replace '### KCD_INSERT_CODE ###' with KCD block when present.
    - AUTO: otherwise, insert before PAUSE_BASE if present; else insert at top of gcode.
    """
    if not os.path.exists(path):
        return False, "File not found"

    try:
        with open(path, "r") as f:
            lines = f.readlines()
    except Exception as e:
        return False, f"Read error: {e}"

    start_index, end_index = _find_macro_block(lines, macro_name)
    if start_index == -1:
        return False, f"Macro '{macro_name}' not found"

    macro_block = lines[start_index:end_index]

    if any(KCD_START_PAUSE_MARKER in ln for ln in macro_block) and any(KCD_END_PAUSE_MARKER in ln for ln in macro_block):
        return True, "Call already present"

    macro_block = _clean_marked_block(macro_block, KCD_START_PAUSE_MARKER, KCD_END_PAUSE_MARKER)
    lines[start_index:end_index] = macro_block
    end_index = start_index + len(macro_block)

    for i in range(start_index, end_index):
        if macro_to_call in lines[i]:
            return True, "Call already present"

    insertion_index, end_index, used_hook = _apply_insert_hook_or_auto(
        lines, start_index, end_index, patterns_for_auto=["PAUSE_BASE"]
    )

    macro_block = lines[start_index:end_index]
    indent = _detect_indent(macro_block)

    kcd_block = [
        f"{indent}{KCD_START_PAUSE_MARKER} - DO NOT MODIFY OR DELETE THIS BLOCK ###\n",
        f"{indent}# KCD: log pause\n",
        f"{indent}{macro_to_call}\n",
        f"{indent}{KCD_END_PAUSE_MARKER} ###\n",
        "\n",
    ]

    if insertion_index is not None:
        lines[insertion_index:insertion_index] = kcd_block
    else:
        # AUTO: insert before PAUSE_BASE if present, else after gcode:
        insert_at = None
        for i in range(start_index, end_index):
            if "PAUSE_BASE" in lines[i]:
                insert_at = i
                break
        if insert_at is not None:
            lines[insert_at:insert_at] = kcd_block
        else:
            gcode_start = -1
            for i in range(start_index, end_index):
                if lines[i].strip().startswith("gcode:"):
                    gcode_start = i
                    break
            if gcode_start == -1:
                gcode_line_index = start_index + 1
                lines.insert(gcode_line_index, "gcode:\n")
                lines[gcode_line_index + 1:gcode_line_index + 1] = kcd_block
            else:
                lines[gcode_start + 1:gcode_start + 1] = kcd_block

    try:
        with open(path, "w") as f:
            f.writelines(lines)
        return True, "Success"
    except Exception as e:
        return False, f"Write error: {e}"


def insert_resume_macro_call(macro_name, path, macro_to_call):
    """
    Insert KCD_JOB_RESUME into a RESUME macro without breaking existing behavior.

    - CLEAN: remove previously inserted RESUME block between RESUME markers.
    - HOOK: replace '### KCD_INSERT_CODE ###' with KCD block when present.
    - AUTO: otherwise, insert before RESUME_BASE if present; else insert at top of gcode.
    """
    if not os.path.exists(path):
        return False, "File not found"

    try:
        with open(path, "r") as f:
            lines = f.readlines()
    except Exception as e:
        return False, f"Read error: {e}"

    start_index, end_index = _find_macro_block(lines, macro_name)
    if start_index == -1:
        return False, f"Macro '{macro_name}' not found"

    macro_block = lines[start_index:end_index]

    if any(KCD_START_RESUME_MARKER in ln for ln in macro_block) and any(KCD_END_RESUME_MARKER in ln for ln in macro_block):
        return True, "Call already present"

    macro_block = _clean_marked_block(macro_block, KCD_START_RESUME_MARKER, KCD_END_RESUME_MARKER)
    lines[start_index:end_index] = macro_block
    end_index = start_index + len(macro_block)

    for i in range(start_index, end_index):
        if macro_to_call in lines[i]:
            return True, "Call already present"

    insertion_index, end_index, used_hook = _apply_insert_hook_or_auto(
        lines, start_index, end_index, patterns_for_auto=["RESUME_BASE"]
    )

    macro_block = lines[start_index:end_index]
    indent = _detect_indent(macro_block)

    kcd_block = [
        f"{indent}{KCD_START_RESUME_MARKER} - DO NOT MODIFY OR DELETE THIS BLOCK ###\n",
        f"{indent}# KCD: log resume\n",
        f"{indent}{macro_to_call}\n",
        f"{indent}{KCD_END_RESUME_MARKER} ###\n",
        "\n",
    ]

    if insertion_index is not None:
        lines[insertion_index:insertion_index] = kcd_block
    else:
        insert_at = None
        for i in range(start_index, end_index):
            if "RESUME_BASE" in lines[i]:
                insert_at = i
                break
        if insert_at is not None:
            lines[insert_at:insert_at] = kcd_block
        else:
            gcode_start = -1
            for i in range(start_index, end_index):
                if lines[i].strip().startswith("gcode:"):
                    gcode_start = i
                    break
            if gcode_start == -1:
                gcode_line_index = start_index + 1
                lines.insert(gcode_line_index, "gcode:\n")
                lines[gcode_line_index + 1:gcode_line_index + 1] = kcd_block
            else:
                lines[gcode_start + 1:gcode_start + 1] = kcd_block

    try:
        with open(path, "w") as f:
            f.writelines(lines)
        return True, "Success"
    except Exception as e:
        return False, f"Write error: {e}"


def insert_cancel_macro_call(macro_name, path, macro_to_call):
    """
    Inserts a macro call line (e.g., 'KCD_JOB_CANCEL') into an existing cancel macro's gcode block.

    - CLEAN: remove previously inserted CANCEL_PRINT block between the CANCEL markers.
    - HOOK: if '### KCD_INSERT_CODE ###' exists inside the macro, we replace that line
      with the KCD block.
    - AUTO: otherwise, insert before CANCEL_PRINT_BASE if present; else insert at top of gcode.
    """
    if not os.path.exists(path):
        return False, "File not found"

    try:
        with open(path, "r") as f:
            lines = f.readlines()
    except Exception as e:
        return False, f"Read error: {e}"

    start_index, end_index = _find_macro_block(lines, macro_name)
    if start_index == -1:
        return False, f"Macro '{macro_name}' not found"

    macro_block = lines[start_index:end_index]

    # If a KCD CANCEL_PRINT block is already present, skip repatching.
    if any(KCD_START_CANCEL_MARKER in ln for ln in macro_block) and any(
        KCD_END_CANCEL_MARKER in ln for ln in macro_block
    ):
        return True, "Call already present"

    # CLEAN: remove any previous KCD CANCEL_PRINT block
    macro_block = _clean_marked_block(macro_block, KCD_START_CANCEL_MARKER, KCD_END_CANCEL_MARKER)
    lines[start_index:end_index] = macro_block
    end_index = start_index + len(macro_block)

    # Check if macro already calls macro_to_call (without our markers)
    for i in range(start_index, end_index):
        if macro_to_call in lines[i]:
            return True, "Call already present"

    # Determine insertion index:
    # 1) Try user hook
    insertion_index, end_index, used_hook = _apply_insert_hook_or_auto(
        lines, start_index, end_index, patterns_for_auto=None
    )

    # Determine indentation
    macro_block = lines[start_index:end_index]
    indent = _detect_indent(macro_block)

    # Build KCD CANCEL block
    kcd_block = [
        f"{indent}{KCD_START_CANCEL_MARKER} - DO NOT MODIFY OR DELETE THIS BLOCK ###\n",
        f"{indent}# KCD: log print cancel\n",
        f"{indent}{macro_to_call}\n",
        f"{indent}{KCD_END_CANCEL_MARKER} ###\n",
        "\n",
    ]

    if insertion_index is not None:
        # Hook used: we already removed the hook line; just insert here
        lines[insertion_index:insertion_index] = kcd_block
    else:
        # AUTO mode:
        # Prefer inserting before CANCEL_PRINT_BASE if present.
        cancel_base_idx = None
        for i in range(start_index, end_index):
            if re.search(r"\bCANCEL_PRINT_BASE\b", lines[i], re.IGNORECASE):
                cancel_base_idx = i
                break
        if cancel_base_idx is not None:
            lines[cancel_base_idx:cancel_base_idx] = kcd_block
        else:
            # Otherwise insert immediately after 'gcode:' line, or create 'gcode:'.
            gcode_start = -1
            for i in range(start_index, end_index):
                if lines[i].strip().startswith("gcode:"):
                    gcode_start = i
                    break

            if gcode_start == -1:
                gcode_line_index = start_index + 1
                lines.insert(gcode_line_index, "gcode:\n")
                lines[gcode_line_index + 1:gcode_line_index + 1] = kcd_block
            else:
                lines[gcode_start + 1:gcode_start + 1] = kcd_block

    try:
        with open(path, "w") as f:
            f.writelines(lines)
        return True, "Success"
    except Exception as e:
        return False, f"Write error: {e}"


def create_default_cancel_print_macro(path, printer_name):
    """
    Appends a default CANCEL_PRINT macro to the file.
    """
    content = f"""

[gcode_macro CANCEL_PRINT]
description: Cancel print macro (auto-generated)
rename_existing: CANCEL_PRINT_BASE
gcode:
    {KCD_START_CANCEL_MARKER} - DO NOT MODIFY OR DELETE THIS BLOCK ###
    # KCD: log print cancel
    KCD_JOB_CANCEL
    {KCD_END_CANCEL_MARKER} ###

    CANCEL_PRINT_BASE
"""
    try:
        with open(path, "a") as f:
            f.write(content)
        return True, "Created new CANCEL_PRINT macro."
    except Exception as e:
        return False, f"Failed to append macro: {e}"


def prompt_cancel_macro_insertion(printer_name, config_dir, default_macro=None, default_file=None):
    """
    Interactive wizard to find and patch a cancel macro to call KCD_JOB_CANCEL.
    Returns (macro_name, target_file) if patched or already present, else (None, None).
    """
    print("\n=== Automatic Cancel Macro Integration ===")
    print("Scanning for CANCEL_PRINT macros in all .cfg files...")

    # Fast path if defaults provided
    if default_macro and default_file:
        full_path = os.path.join(config_dir, default_file)
        success, msg = insert_cancel_macro_call(default_macro, full_path, "KCD_JOB_CANCEL")
        if success:
            if msg == "Call already present":
                print("  - Call already present; skipping.")
            else:
                print(f"  - Patched {default_macro} in {default_file}.")
            return default_macro, default_file
        else:
            print(f"  - Default cancel macro patch failed ({msg}), falling back to manual selection.")

    macros = find_cancel_macros_in_dir(config_dir)

    target_macro = None
    target_file = None

    if macros:
        print(f"Found {len(macros)} potential cancel macro(s):")
        for i, (fname, name, line) in enumerate(macros):
            print(f"  {i+1}) {name} in {fname} (line {line})")
        print("  0) None of these / Enter manually")
        choice = input("Select cancel macro to patch [1] (or 's' to skip): ").strip()
        if choice.lower() == "s":
            print("Skipping cancel macro integration.")
            return None, None
        if not choice:
            choice = "1"
        try:
            idx = int(choice)
            if 1 <= idx <= len(macros):
                target_file = macros[idx - 1][0]
                target_macro = macros[idx - 1][1]
        except ValueError:
            target_macro = None
    else:
        print("No obvious CANCEL_PRINT macros found.")

    if not target_macro:
        print("\nOptions:")
        print("  1) Enter macro name manually (assumes it is in printer.cfg)")
        print("  2) Create a new default [gcode_macro CANCEL_PRINT] in printer.cfg")
        print("  3) Skip")

        ans = input("Select option [2]: ").strip()
        if ans == "1":
            target_macro = input("Enter macro name: ").strip()
            target_file = "printer.cfg"
        elif ans == "3" or ans.lower() == "s":
            print("Skipping cancel macro integration.")
            return None, None
        else:
            target_macro = "CANCEL_PRINT"
            target_file = "printer.cfg"
            full_path = os.path.join(config_dir, target_file)
            success, msg = create_default_cancel_print_macro(full_path, printer_name)
            if success:
                print(f"  - {msg}")
            else:
                print(f"  - Failed: {msg}")
            return target_macro, target_file

    if not target_macro:
        return None, None

    full_path = os.path.join(config_dir, target_file)
    print(f"Attempting to patch cancel macro '{target_macro}' in {target_file}...")
    success, msg = insert_cancel_macro_call(target_macro, full_path, "KCD_JOB_CANCEL")
    if success:
        if msg == "Call already present":
            print(f"  - {msg}, skipping.")
        else:
            print("  - Success! Added KCD_JOB_CANCEL via KCD block.")
    else:
        print(f"  - Failed: {msg}")
        print("  - Please add the following line to your cancel macro manually:")
        print("    KCD_JOB_CANCEL")
        return None, None

    return target_macro, target_file

def insert_macro_call(macro_name, path, macro_to_call):
    """
    Inserts a macro call line (e.g., 'KCD_JOB_START') into an existing macro's gcode block.

    - CLEAN: remove previously inserted KCD START block between the START/END markers.
    - HOOK: if '### KCD_INSERT_CODE ###' exists inside the macro, we replace that line
      with the KCD block.
    - AUTO: otherwise, insert right after 'gcode:' (or create a 'gcode:' section).
    """
    if not os.path.exists(path):
        return False, "File not found"

    try:
        with open(path, "r") as f:
            lines = f.readlines()
    except Exception as e:
        return False, f"Read error: {e}"

    start_index, end_index = _find_macro_block(lines, macro_name)
    if start_index == -1:
        return False, f"Macro '{macro_name}' not found"

    macro_block = lines[start_index:end_index]

    # If a KCD START_PRINT block is already present, skip repatching.
    if any(KCD_START_START_MARKER in ln for ln in macro_block) and any(KCD_END_START_MARKER in ln for ln in macro_block):
        return True, "Call already present"

    # CLEAN: remove any previous KCD START_PRINT block
    macro_block = _clean_marked_block(macro_block, KCD_START_START_MARKER, KCD_END_START_MARKER)
    lines[start_index:end_index] = macro_block
    end_index = start_index + len(macro_block)

    # Check if macro already calls macro_to_call (without our markers)
    for i in range(start_index, end_index):
        if macro_to_call in lines[i]:
            return True, "Call already present"

    # Determine insertion index:
    # 1) Try user hook
    insertion_index, end_index, used_hook = _apply_insert_hook_or_auto(
        lines, start_index, end_index, patterns_for_auto=None
    )

    # Determine indentation
    macro_block = lines[start_index:end_index]
    indent = _detect_indent(macro_block)

    # Build KCD START block
    kcd_block = [
        f"{indent}{KCD_START_START_MARKER} - DO NOT MODIFY OR DELETE THIS BLOCK ###\n",
        f"{indent}# KCD: log job start\n",
        f"{indent}{macro_to_call}\n",
        f"{indent}{KCD_END_START_MARKER} ###\n",
        "\n",
    ]

    if insertion_index is not None:
        # Hook used: we already removed the hook line; just insert here
        lines[insertion_index:insertion_index] = kcd_block
    else:
        # AUTO mode: insert immediately after 'gcode:' line, or create 'gcode:'
        gcode_start = -1
        for i in range(start_index, end_index):
            if lines[i].strip().startswith("gcode:"):
                gcode_start = i
                break

        if gcode_start == -1:
            # No gcode: line, create one
            gcode_line_index = start_index + 1
            lines.insert(gcode_line_index, "gcode:\n")
            lines[gcode_line_index + 1:gcode_line_index + 1] = kcd_block
        else:
            # Insert right after gcode:
            lines[gcode_start + 1:gcode_start + 1] = kcd_block

    try:
        with open(path, "w") as f:
            f.writelines(lines)
        return True, "Success"
    except Exception as e:
        return False, f"Write error: {e}"


def create_default_print_start_macro(path, printer_name):
    """
    Appends a default PRINT_START macro to the file.
    """
    content = f"""

[gcode_macro PRINT_START]
description: Default start-of-print macro (auto-generated)
gcode:
    {KCD_START_START_MARKER} - DO NOT MODIFY OR DELETE THIS BLOCK ###
    # KCD: log job start
    KCD_JOB_START
    {KCD_END_START_MARKER} ###

    # TODO: add your own start moves here (homing, purge, etc.)
"""
    try:
        with open(path, "a") as f:
            f.write(content)
        return True, "Created new PRINT_START macro."
    except Exception as e:
        return False, f"Failed to append macro: {e}"


def prompt_start_macro_insertion(printer_name, config_dir, default_macro=None, default_file=None):
    """
    Interactive wizard to find and patch a start macro to call KCD_JOB_START.
    Returns (macro_name, target_file) if patched or already present, else (None, None).
    """
    print("\n=== Automatic Start Macro Integration ===")
    print("Scanning for print start macros in all .cfg files...")

    # Fast path if defaults provided
    if default_macro and default_file:
        full_path = os.path.join(config_dir, default_file)
        success, msg = insert_macro_call(default_macro, full_path, "KCD_JOB_START")
        if success:
            if msg == "Call already present":
                print("  - Call already present; skipping.")
            else:
                print(f"  - Patched {default_macro} in {default_file}.")
            return default_macro, default_file
        else:
            print(f"  - Default start macro patch failed ({msg}), falling back to manual selection.")

    macros = find_start_macros_in_dir(config_dir)

    target_macro = None
    target_file = None

    if macros:
        print(f"Found {len(macros)} potential start macro(s):")
        for i, (fname, name, line) in enumerate(macros):
            print(f"  {i+1}) {name} in {fname} (line {line})")
        print("  0) None of these / Enter manually")
        choice = input("Select start macro to patch [1] (or 's' to skip): ").strip()
        if choice.lower() == "s":
            print("Skipping start macro integration.")
            return None, None
        if not choice:
            choice = "1"
        try:
            idx = int(choice)
            if 1 <= idx <= len(macros):
                target_file = macros[idx - 1][0]
                target_macro = macros[idx - 1][1]
        except ValueError:
            target_macro = None
    else:
        print("No obvious PRINT_START or START_PRINT macros found.")

    if not target_macro:
        print("\nOptions:")
        print("  1) Enter macro name manually (assumes it is in printer.cfg)")
        print("  2) Create a new default [gcode_macro PRINT_START] in printer.cfg")
        print("  3) Skip")

        ans = input("Select option [2]: ").strip()
        if ans == "1":
            target_macro = input("Enter macro name: ").strip()
            target_file = "printer.cfg"
        elif ans == "3" or ans.lower() == "s":
            print("Skipping start macro integration.")
            return None, None
        else:
            target_macro = "PRINT_START"
            target_file = "printer.cfg"
            full_path = os.path.join(config_dir, target_file)
            success, msg = create_default_print_start_macro(full_path, printer_name)
            if success:
                print(f"  - {msg}")
            else:
                print(f"  - Failed: {msg}")
            return target_macro, target_file

    if not target_macro:
        return None, None

    full_path = os.path.join(config_dir, target_file)
    print(f"Attempting to patch start macro '{target_macro}' in {target_file}...")
    success, msg = insert_macro_call(target_macro, full_path, "KCD_JOB_START")
    if success:
        if msg == "Call already present":
            print(f"  - {msg}, skipping.")
        else:
            print("  - Success! Added KCD_JOB_START via KCD block.")
    else:
        print(f"  - Failed: {msg}")
        print("  - Please add the following line to your start macro manually:")
        print("    KCD_JOB_START")
        return None, None

    return target_macro, target_file

def prompt_pause_macro_insertion(printer_name, config_dir, default_macro=None, default_file=None):
    """
    Interactive wizard to find and patch a pause macro to call KCD_JOB_PAUSE.
    Returns (macro_name, target_file) if patched or already present, else (None, None).
    """
    print("\n=== Automatic Pause Macro Integration ===")
    print("Scanning for PAUSE macros in all .cfg files...")

    if default_macro and default_file:
        full_path = os.path.join(config_dir, default_file)
        success, msg = insert_pause_macro_call(default_macro, full_path, "KCD_JOB_PAUSE")
        if success:
            if msg == "Call already present":
                print("  - Call already present; skipping.")
            else:
                print(f"  - Patched {default_macro} in {default_file}.")
            return default_macro, default_file
        else:
            print(f"  - Default pause macro patch failed ({msg}), falling back to manual selection.")

    macros = find_pause_macros_in_dir(config_dir)
    if not macros:
        print("No obvious PAUSE macros found. Skipping pause integration.")
        return None, None

    print(f"Found {len(macros)} potential pause macro(s):")
    for i, (fname, name, line) in enumerate(macros):
        print(f"  {i+1}) {name} in {fname} (line {line})")
    print("  0) Skip")
    choice = input("Select pause macro to patch [1] (or 's' to skip): ").strip()
    if choice.lower() == "s" or choice == "0":
        print("Skipping pause macro integration.")
        return None, None
    if choice == "":
        choice = "1"
    if not choice.isdigit():
        print("Invalid choice; skipping pause macro integration.")
        return None, None
    idx = int(choice)
    if idx < 1 or idx > len(macros):
        print("Invalid selection; skipping pause macro integration.")
        return None, None

    target_file, target_macro, _line = macros[idx - 1]
    full_path = os.path.join(config_dir, target_file)

    print(f"Attempting to patch pause macro '{target_macro}' in {target_file}...")
    success, msg = insert_pause_macro_call(target_macro, full_path, "KCD_JOB_PAUSE")
    if success:
        if msg == "Call already present":
            print(f"  - {msg}, skipping.")
        else:
            print("  - Success! Added KCD_JOB_PAUSE via KCD block.")
        return target_macro, target_file
    print(f"  - Failed: {msg}")
    print("  - Please add the following line to your pause macro manually:")
    print("    KCD_JOB_PAUSE")
    return None, None


def prompt_resume_macro_insertion(printer_name, config_dir, default_macro=None, default_file=None):
    """
    Interactive wizard to find and patch a resume macro to call KCD_JOB_RESUME.
    Returns (macro_name, target_file) if patched or already present, else (None, None).
    """
    print("\n=== Automatic Resume Macro Integration ===")
    print("Scanning for RESUME macros in all .cfg files...")

    if default_macro and default_file:
        full_path = os.path.join(config_dir, default_file)
        success, msg = insert_resume_macro_call(default_macro, full_path, "KCD_JOB_RESUME")
        if success:
            if msg == "Call already present":
                print("  - Call already present; skipping.")
            else:
                print(f"  - Patched {default_macro} in {default_file}.")
            return default_macro, default_file
        else:
            print(f"  - Default resume macro patch failed ({msg}), falling back to manual selection.")

    macros = find_resume_macros_in_dir(config_dir)
    if not macros:
        print("No obvious RESUME macros found. Skipping resume integration.")
        return None, None

    print(f"Found {len(macros)} potential resume macro(s):")
    for i, (fname, name, line) in enumerate(macros):
        print(f"  {i+1}) {name} in {fname} (line {line})")
    print("  0) Skip")
    choice = input("Select resume macro to patch [1] (or 's' to skip): ").strip()
    if choice.lower() == "s" or choice == "0":
        print("Skipping resume macro integration.")
        return None, None
    if choice == "":
        choice = "1"
    if not choice.isdigit():
        print("Invalid choice; skipping resume macro integration.")
        return None, None
    idx = int(choice)
    if idx < 1 or idx > len(macros):
        print("Invalid selection; skipping resume macro integration.")
        return None, None

    target_file, target_macro, _line = macros[idx - 1]
    full_path = os.path.join(config_dir, target_file)

    print(f"Attempting to patch resume macro '{target_macro}' in {target_file}...")
    success, msg = insert_resume_macro_call(target_macro, full_path, "KCD_JOB_RESUME")
    if success:
        if msg == "Call already present":
            print(f"  - {msg}, skipping.")
        else:
            print("  - Success! Added KCD_JOB_RESUME via KCD block.")
        return target_macro, target_file
    print(f"  - Failed: {msg}")
    print("  - Please add the following line to your resume macro manually:")
    print("    KCD_JOB_RESUME")
    return None, None

def run_macro_integration(printer_name: str, config_dir: str) -> None:
    """
    Run both END and START macro integration for a given config directory.

    Thin wrapper over prompt_macro_insertion and prompt_start_macro_insertion.
    """
    prompt_macro_insertion(printer_name, config_dir)
    prompt_start_macro_insertion(printer_name, config_dir)
    prompt_cancel_macro_insertion(printer_name, config_dir)
    prompt_pause_macro_insertion(printer_name, config_dir)
    prompt_resume_macro_insertion(printer_name, config_dir)
    prompt_filament_change_macro_insertion(printer_name, config_dir)
