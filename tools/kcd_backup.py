"""
Create and manage backups for Klipper Cost Dashboard.

Writes `kcd_backup_YYYYmmdd_HHMMSS.tar.gz` under `data/backups/` and enforces
retention if requested.
"""

from __future__ import annotations

import argparse
import os


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a KCD backup archive")
    parser.add_argument(
        "--keep",
        type=int,
        default=None,
        help="Retention count (delete older backups beyond this number).",
    )
    args = parser.parse_args()

    from core.backup import create_backup_archive

    path = create_backup_archive(keep=args.keep)
    print(f"Backup created: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

