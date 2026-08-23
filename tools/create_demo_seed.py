"""Generate a deterministic minimal demo seed; no binary seed is committed."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import tempfile

# Avoid normal-mode import side effects. This tool still uses an explicit context.
os.environ.setdefault("KCD_RUNTIME_MODE", "demo")

from core import db as db_module  # noqa: E402
from core.workspace_context import WorkspaceContext  # noqa: E402


def create_seed(data_root: Path, seed_name: str) -> Path:
    root = data_root.expanduser().resolve()
    if not root.is_absolute() or root.is_symlink():
        raise ValueError("data root must be an absolute, non-symlinked directory")
    if Path(seed_name).name != seed_name or seed_name in {"", ".", ".."}:
        raise ValueError("seed name must be a simple directory name")
    seeds = root / "seeds"
    seeds.mkdir(parents=True, exist_ok=True)
    destination = seeds / seed_name
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite existing seed: {destination}")
    temporary = Path(tempfile.mkdtemp(prefix=".seed-", dir=seeds))
    context = WorkspaceContext(
        "demo", "seed-builder", temporary, temporary / "kcd.db", temporary / "cache",
        temporary / "exports", temporary / "temp", temporary / "uploads", seed_name, "sql",
    )
    try:
        with closing(db_module.connect_db(context)) as conn:
            db_module.apply_migrations(conn)
            now = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO printers(name, created_at, updated_at) VALUES(?,?,?)",
                ("Demo Printer", now, now),
            )
            conn.execute(
                "INSERT INTO projects(project_uid,name,created_at,updated_at) VALUES(?,?,?,?)",
                ("basic-project", "Basic Project", now, now),
            )
            conn.commit()
        for name in ("cache", "exports", "temp", "uploads"):
            (temporary / name).mkdir()
        os.replace(temporary, destination)
        return destination
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--seed-name", default="basic-v1")
    args = parser.parse_args()
    print(create_seed(args.data_root, args.seed_name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
