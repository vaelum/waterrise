"""Files several processes use: an exclusive lock, and a replace that waits out a reader.

The lock is `flock` on POSIX. On Windows it is `msvcrt.locking` on the file's
first byte: `LK_LOCK` gives up after ten seconds, so a blocking lock retries
`LK_NBLCK` instead. Without either module there is no locking, and that is an
error, never a lock silently skipped.

On Windows a file another process has open can't be replaced or removed:
`os.replace` and `unlink` raise PermissionError until it is closed. Readers
here hold files for milliseconds, so `replace` and `remove` retry for a while
before giving up.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Iterator

from .errors import WaterriseError

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None
try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None

RETRY = 0.01  # seconds between attempts on Windows
REPLACE_FOR = 1.0  # seconds a replace keeps trying on Windows
WINDOWS = os.name == "nt"


def acquire(path: Path, wait: bool = True) -> IO[bytes]:
    """The lock on `path`, created if missing; held until `release`.
    With `wait=False`, BlockingIOError if another process holds it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+b")
    try:
        if fcntl is not None:
            fcntl.flock(fh, fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif msvcrt is not None:
            while True:
                fh.seek(0)
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if not wait:
                        raise BlockingIOError(f"{path} is locked") from None
                    time.sleep(RETRY)
        else:
            raise WaterriseError("no file locking on this platform: neither fcntl nor msvcrt")
    except BaseException:
        fh.close()
        raise
    return fh


def release(fh: IO[bytes]) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(fh, fcntl.LOCK_UN)
        else:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        fh.close()


@contextmanager
def held(path: Path, wait: bool = True) -> Iterator[IO[bytes]]:
    fh = acquire(path, wait=wait)
    try:
        yield fh
    finally:
        release(fh)


def remove(path: Path) -> None:
    """`unlink`, missing or not, retried on Windows while another process has
    the file open: the service's service.json, read every 50 ms by a `stop`
    waiting for it to go, stayed for good (2026-10-21, in the Windows VM)."""
    deadline = time.monotonic() + REPLACE_FOR
    while True:
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if not WINDOWS or time.monotonic() >= deadline:
                raise
            time.sleep(RETRY)


def replace(src: Path | str, dst: Path | str) -> None:
    """`os.replace`, retried on Windows while another process has `dst` open."""
    deadline = time.monotonic() + REPLACE_FOR
    while True:
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if not WINDOWS or time.monotonic() >= deadline:
                raise
            time.sleep(RETRY)
