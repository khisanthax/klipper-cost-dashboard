"""Generate a deterministic minimal demo seed; no binary seed is committed."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import sys
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BASIC_SEED_NAME = "basic-v1"


def _seed_support():
    """Import seed support without leaking a runtime-mode change to callers."""
    previous = os.environ.get("KCD_RUNTIME_MODE")
    os.environ["KCD_RUNTIME_MODE"] = "demo"
    try:
        from core.demo.basic_seed import (
            build_basic_seed_database, validate_basic_seed, write_seed_manifest,
        )
    finally:
        if previous is None:
            os.environ.pop("KCD_RUNTIME_MODE", None)
        else:
            os.environ["KCD_RUNTIME_MODE"] = previous
    return build_basic_seed_database, validate_basic_seed, write_seed_manifest


def create_seed(data_root: Path, seed_name: str) -> Path:
    build_basic_seed_database, validate_basic_seed, write_seed_manifest = _seed_support()
    supplied_root = data_root.expanduser()
    if not supplied_root.is_absolute() or supplied_root.is_symlink():
        raise ValueError("data root must be an absolute, non-symlinked directory")
    if not supplied_root.exists() or not supplied_root.is_dir():
        raise ValueError("data root must already exist as a directory")
    root = supplied_root.resolve(strict=True)
    if Path(seed_name).name != seed_name or seed_name in {"", ".", ".."}:
        raise ValueError("seed name must be a simple directory name")
    seeds = root / "seeds"
    seeds.mkdir(parents=True, exist_ok=True)
    destination = seeds / seed_name
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite existing seed: {destination}")
    temporary = Path(tempfile.mkdtemp(prefix=".seed-", dir=seeds))
    try:
        result = build_basic_seed_database(temporary, seed_name)
        write_seed_manifest(temporary, seed_name, result)
        validate_basic_seed(temporary, expected_seed_name=seed_name)
        os.replace(temporary, destination)
        return destination
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--seed-name", default=BASIC_SEED_NAME)
    args = parser.parse_args()
    print(create_seed(args.data_root, args.seed_name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
