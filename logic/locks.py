from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
import os, time

@contextmanager
def exclusive_lock(lock_path: Path, stale_after_seconds: int = 900):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            fd=os.open(str(lock_path), os.O_CREAT|os.O_EXCL|os.O_WRONLY)
            os.write(fd, f'{os.getpid()} {time.time()}'.encode())
            break
        except FileExistsError:
            try:
                age=time.time()-lock_path.stat().st_mtime
                if age > stale_after_seconds:
                    lock_path.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            raise RuntimeError(f'Workbook lock already exists: {lock_path}')
    try:
        yield
    finally:
        try: os.close(fd)
        except Exception: pass
        lock_path.unlink(missing_ok=True)
