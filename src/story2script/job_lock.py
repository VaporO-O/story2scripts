"""Local filesystem execution lock, released by the OS if a worker process dies.

This protects same-host SQLite recovery. It is not a distributed queue/lock.
"""

import hashlib
import os
from pathlib import Path


class JobFileLock:
    def __init__(self, database: Path, job_id: str):
        directory = database.parent / (database.name + ".locks")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (hashlib.sha256(job_id.encode()).hexdigest() + ".lock")
        self.file = path.open("a+b")
        # Both flock and Windows byte-range locking permit locking past EOF.
        # Do not initialize the file with a byte: a concurrent opener can already
        # own that byte, making the initialization write fail on Windows.
        self.locked = False

    def acquire(self):
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (11, 13, 35, 36):
                self.close()
                raise
            return False
        self.locked = True
        return True

    def close(self):
        if self.file.closed:
            return
        if self.locked:
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.locked = False
        self.file.close()
