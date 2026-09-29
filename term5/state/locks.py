from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path


class WorkspaceWriteLock:
    """Small cross-process advisory lock around local write transactions."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = self.path.open("a+b")
        try:
            if os.name == "nt":  # pragma: no cover - exercised on Windows
                import msvcrt
                fh.seek(0)
                if fh.tell() == 0:
                    fh.write(b"0")
                    fh.flush()
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                if os.name == "nt":  # pragma: no cover
                    import msvcrt
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            finally:
                fh.close()
