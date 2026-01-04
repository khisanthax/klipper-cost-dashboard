"""
Backfill jobs.thumbnail in SQLite from existing cached thumbnail files.

This script is idempotent and safe to re-run. It updates rows where
jobs.thumbnail is NULL/blank, using the canonical thumbnail token.
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime

from core import db as db_module
from core import thumbnails as thumbs
from core.config import DATA_DIR


def _safe_dir(name: str) -> str:
    s = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(name or "").strip())
    return s or "unknown"


def _thumb_path(printer: str, token: str, size: str) -> str:
    safe_printer = _safe_dir(printer)
    return os.path.join(DATA_DIR, "thumb_cache", safe_printer, f"{token}_{size}.png")


def main() -> int:
    conn = db_module.connect_db()
    db_module.apply_migrations(conn)

    rows = conn.execute(
        """
        SELECT j.job_uid, p.name AS printer, p.moonraker_url, j.filename, j.thumbnail
          FROM jobs j
          JOIN printers p ON j.printer_id = p.id
         WHERE j.thumbnail IS NULL OR TRIM(j.thumbnail) = ''
        """
    ).fetchall()

    scanned = 0
    fetched = 0
    saved = 0
    updated = 0
    missing_metadata = 0
    download_fail = 0
    errors = 0

    now = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    for row in rows:
        scanned += 1
        job_uid = str(row["job_uid"] or "").strip()
        printer = str(row["printer"] or "").strip()
        base_url = str(row["moonraker_url"] or "").strip()
        filename = str(row["filename"] or "").strip()
        if not job_uid or not printer or not filename:
            missing_metadata += 1
            continue

        try:
            token = thumbs.compute_thumbnail_token(printer, filename, base_url=base_url)
            small_path = _thumb_path(printer, token, "small")
            card_path = _thumb_path(printer, token, "card")

            found = os.path.exists(small_path) or os.path.exists(card_path)

            if not found:
                legacy_small = thumbs.compute_legacy_thumbnail_token(printer, filename, "small", base_url=base_url)
                legacy_card = thumbs.compute_legacy_thumbnail_token(printer, filename, "card", base_url=base_url)
                legacy_small_path = _thumb_path(printer, legacy_small, "small")
                legacy_card_path = _thumb_path(printer, legacy_card, "card")

                if os.path.exists(legacy_small_path):
                    try:
                        os.makedirs(os.path.dirname(small_path), exist_ok=True)
                        if not os.path.exists(small_path):
                            shutil.copy2(legacy_small_path, small_path)
                        found = True
                    except Exception:
                        pass
                if os.path.exists(legacy_card_path):
                    try:
                        os.makedirs(os.path.dirname(card_path), exist_ok=True)
                        if not os.path.exists(card_path):
                            shutil.copy2(legacy_card_path, card_path)
                        found = True
                    except Exception:
                        pass

            if not found:
                if not base_url:
                    missing_metadata += 1
                    continue

                meta = thumbs._metadata_thumbnails(printer, filename, size_hint="small", base_url=base_url)
                if not meta:
                    missing_metadata += 1
                    continue

                fetched += 1
                small_cached = thumbs.get_cached_thumbnail_path(
                    printer,
                    filename,
                    "small",
                    base_url=base_url,
                )
                card_cached = thumbs.get_cached_thumbnail_path(
                    printer,
                    filename,
                    "card",
                    base_url=base_url,
                )
                if small_cached or card_cached:
                    found = True
                    saved += 1
                else:
                    download_fail += 1
                    continue

            if found:
                conn.execute(
                    "UPDATE jobs SET thumbnail = ?, updated_at = ? WHERE job_uid = ?",
                    (token, now, job_uid),
                )
                updated += 1
        except Exception:
            errors += 1

    conn.commit()

    print(
        "Backfill complete: "
        f"scanned={scanned} fetched={fetched} saved={saved} updated={updated} "
        f"missing_metadata={missing_metadata} download_fail={download_fail} errors={errors}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
