"""
Backfill jobs.thumbnail in SQLite from existing cached thumbnail files.

This script is idempotent and safe to re-run. It updates rows where
jobs.thumbnail is NULL/blank, using the canonical thumbnail token.
"""
from __future__ import annotations

import argparse
import os
import shutil
from datetime import datetime
from typing import Dict, List, Optional

from core import db as db_module
from core import thumbnails as thumbs
from core import moonraker
from core.config import DATA_DIR


def _safe_dir(name: str) -> str:
    s = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(name or "").strip())
    return s or "unknown"


def _thumb_path(printer: str, token: str, size: str) -> str:
    safe_printer = _safe_dir(printer)
    return os.path.join(DATA_DIR, "thumb_cache", safe_printer, f"{token}_{size}.png")


def _walk_moonraker_items(items: List[dict], prefix: str = "") -> List[str]:
    paths: List[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("path") or item.get("name") or item.get("filename") or "").strip()
        if not raw:
            continue
        raw = raw.lstrip("/")
        full = raw
        if prefix and not raw.startswith(prefix.rstrip("/") + "/"):
            full = f"{prefix.rstrip('/')}/{raw}"

        children = item.get("items") or item.get("children")
        itype = str(item.get("type") or "").strip().lower()
        if isinstance(children, list) and children:
            paths.extend(_walk_moonraker_items(children, prefix=full))
            continue
        if itype in ("dir", "directory", "folder"):
            continue
        paths.append(full)
    return paths


_list_cache: Dict[str, List[str]] = {}


def _list_moonraker_files(base_url: str) -> List[str]:
    base_url = str(base_url or "").strip()
    if not base_url:
        return []
    if base_url in _list_cache:
        return _list_cache[base_url]

    ok, detail, payload = moonraker.moonraker_get_json(base_url, "/server/files/list", {"root": "gcodes"})
    if not ok or not isinstance(payload, dict):
        _list_cache[base_url] = []
        return []

    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    items = result.get("items") if isinstance(result, dict) else None
    if not isinstance(items, list):
        _list_cache[base_url] = []
        return []

    paths = _walk_moonraker_items(items, prefix="")
    _list_cache[base_url] = paths
    return paths


def _find_moonraker_path(base_url: str, filename: str) -> Optional[str]:
    base_url = str(base_url or "").strip()
    name = str(filename or "").strip()
    if not base_url or not name:
        return None
    target = os.path.basename(name).lower()
    if not target:
        return None
    candidates = [p for p in _list_moonraker_files(base_url) if os.path.basename(p).lower() == target]
    if not candidates:
        return None
    # Prefer the shortest path if multiple exist.
    return sorted(candidates, key=lambda p: (len(p), p))[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill jobs.thumbnail in SQLite from cached thumbnails.")
    parser.add_argument("--diagnostic", action="store_true", help="Print missing metadata diagnostics.")
    args = parser.parse_args()

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
    fallback_searches = 0
    fallback_hits = 0

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
                    if args.diagnostic:
                        print(f"[diag] missing base_url printer={printer} filename={filename}")
                    continue

                meta = thumbs._metadata_thumbnails(printer, filename, size_hint="small", base_url=base_url)
                meta_filename = filename
                if not meta:
                    fallback_searches += 1
                    alt_path = _find_moonraker_path(base_url, filename)
                    if alt_path:
                        fallback_hits += 1
                        meta_filename = alt_path
                        meta = thumbs._metadata_thumbnails(printer, alt_path, size_hint="small", base_url=base_url)
                        if args.diagnostic:
                            print(
                                f"[diag] fallback path printer={printer} filename={filename} alt={alt_path} meta={len(meta)}"
                            )

                if not meta:
                    missing_metadata += 1
                    if args.diagnostic:
                        print(
                            f"[diag] missing metadata printer={printer} filename={filename} base={base_url}"
                        )
                    continue

                fetched += 1
                small_cached = thumbs.get_cached_thumbnail_path(
                    printer,
                    meta_filename,
                    "small",
                    base_url=base_url,
                )
                card_cached = thumbs.get_cached_thumbnail_path(
                    printer,
                    meta_filename,
                    "card",
                    base_url=base_url,
                )
                if small_cached or card_cached:
                    if meta_filename != filename:
                        try:
                            if small_cached and not os.path.exists(small_path):
                                os.makedirs(os.path.dirname(small_path), exist_ok=True)
                                shutil.copy2(small_cached, small_path)
                            if card_cached and not os.path.exists(card_path):
                                os.makedirs(os.path.dirname(card_path), exist_ok=True)
                                shutil.copy2(card_cached, card_path)
                        except Exception:
                            pass
                    found = True
                    saved += 1
                else:
                    download_fail += 1
                    if args.diagnostic:
                        print(
                            f"[diag] download failed printer={printer} filename={meta_filename} base={base_url}"
                        )
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
        f"missing_metadata={missing_metadata} download_fail={download_fail} "
        f"fallback_searches={fallback_searches} fallback_hits={fallback_hits} "
        f"errors={errors}"
    )
    if missing_metadata:
        print(
            "Note: missing_metadata usually means the gcode file is no longer present "
            "in the printer's Moonraker gcodes storage, so thumbnails cannot be recovered."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
