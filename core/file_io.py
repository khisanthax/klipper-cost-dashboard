"""Atomic file replacement with private, recoverable backups."""
import os
from pathlib import Path
import tempfile
import time


def atomic_write(path, content, mode=0o600, backup=True):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        previous = path.read_bytes()
        if previous == content.encode("utf-8"):
            os.chmod(path, mode)
            return
        backup_path = path.with_name(path.name + ".kcd-backup-" + str(time.time_ns()))
        fd = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(previous)
    fd, temporary = tempfile.mkstemp(prefix=".kcd-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
