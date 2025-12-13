"""
Small debug harness for slicer metadata extraction.

Run from repo root:
  python tools/gcode_metadata_harness.py path/to/file.gcode
"""

from __future__ import annotations

import sys

from core.gcode_metadata import extract_gcode_metadata


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python tools/gcode_metadata_harness.py path/to/file.gcode")
        return 2
    path = argv[1]
    meta = extract_gcode_metadata(path)
    print(meta)
    return 0 if meta.found else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

