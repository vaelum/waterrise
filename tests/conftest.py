"""Every test runs in a home of its own, so no test can write into the real one.

`Path.home()` reads HOME on POSIX and USERPROFILE on Windows, and the Windows
defaults live under LOCALAPPDATA and APPDATA; all of them point into the
test's temporary directory. The XDG variables are cleared, so a test sees the
defaults under that home unless it sets them itself.
"""

import os
from pathlib import Path

import pytest

HOME_VARS = ("HOME", "USERPROFILE")
XDG_VARS = ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "WATERRISE_STATE_DIR")


@pytest.fixture
def fake_home(monkeypatch):
    """Point every home variable at `path` and return it."""
    def point(path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        for name in HOME_VARS:
            monkeypatch.setenv(name, str(path))
        monkeypatch.setenv("LOCALAPPDATA", str(path / "AppData" / "Local"))
        monkeypatch.setenv("APPDATA", str(path / "AppData" / "Roaming"))
        for name in XDG_VARS:
            monkeypatch.delenv(name, raising=False)
        return path
    return point


@pytest.fixture(autouse=True)
def _own_home(tmp_path_factory, fake_home):
    return fake_home(tmp_path_factory.mktemp("home"))


class FakeMsvcrt:
    """`msvcrt.locking` as Windows behaves, built on flock so that it really
    excludes: LK_NBLCK fails at once with OSError while another open file holds
    the lock. Only whole-file locks of the first byte, which is all lock.py
    takes."""
    LK_UNLCK, LK_LOCK, LK_NBLCK = 0, 1, 2

    def __init__(self):
        import fcntl
        self._fcntl = fcntl
        self.calls = []

    def locking(self, fd, mode, nbytes):
        assert nbytes == 1 and os.lseek(fd, 0, os.SEEK_CUR) == 0
        self.calls.append(mode)
        if mode == self.LK_UNLCK:
            self._fcntl.flock(fd, self._fcntl.LOCK_UN)
            return
        assert mode == self.LK_NBLCK, "lock.py must not use LK_LOCK, which gives up after 10 s"
        try:
            self._fcntl.flock(fd, self._fcntl.LOCK_EX | self._fcntl.LOCK_NB)
        except BlockingIOError:
            raise PermissionError(13, "Permission denied") from None


class RecordingMsvcrt:
    """The real msvcrt on Windows, recording the calls as FakeMsvcrt does."""

    def __init__(self):
        import msvcrt
        self._msvcrt = msvcrt
        self.LK_UNLCK, self.LK_LOCK, self.LK_NBLCK = msvcrt.LK_UNLCK, msvcrt.LK_LOCK, msvcrt.LK_NBLCK
        self.calls = []

    def locking(self, fd, mode, nbytes):
        self.calls.append(mode)
        return self._msvcrt.locking(fd, mode, nbytes)


@pytest.fixture
def windows_locks(monkeypatch):
    """lock.py's Windows branch, on this machine: on Windows the real msvcrt."""
    from waterrise import lock
    fake = RecordingMsvcrt() if os.name == "nt" else FakeMsvcrt()
    monkeypatch.setattr(lock, "fcntl", None)
    monkeypatch.setattr(lock, "msvcrt", fake)
    return fake


@pytest.fixture(params=["native", "windows"])
def locks(request):
    """Run a test once with this platform's locks and once with Windows'."""
    if request.param == "windows":
        return request.getfixturevalue("windows_locks")
    return None


@pytest.fixture
def registry(monkeypatch):
    """windows.py's registry primitives on a dictionary: {(key, name): (value, kind)}."""
    from waterrise import windows
    store: dict = {}
    sent = []
    monkeypatch.setattr(windows, "_get", lambda key, name: store.get((key, name)))
    monkeypatch.setattr(windows, "_set", lambda key, name, value, kind=None:
                        store.__setitem__((key, name), (value, 1 if kind is None else kind)))
    monkeypatch.setattr(windows, "_delete", lambda key, name: store.pop((key, name), None))
    monkeypatch.setattr(windows, "_expand_kind", lambda: 2)
    monkeypatch.setattr(windows, "_broadcast", lambda: sent.append("Environment"))
    store["broadcasts"] = sent
    return store
