"""What only Windows needs: the user's PATH and the login Run value, both in
the current user's registry (HKEY_CURRENT_USER), so nothing needs an
administrator.

The registry is read and written by the five primitives below and nothing
else, so the tests replace them with a dictionary.
"""

from __future__ import annotations

import ntpath
from pathlib import Path

ENV_KEY = "Environment"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "waterrise"


# --------------------------------------------------------------------------- #
# the primitives
# --------------------------------------------------------------------------- #

def _get(key: str, name: str) -> tuple[str, int] | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            value, kind = winreg.QueryValueEx(k, name)
    except FileNotFoundError:
        return None
    return str(value), kind


def _set(key: str, name: str, value: str, kind: int | None = None) -> None:
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ if kind is None else kind, value)


def _delete(key: str, name: str) -> None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)
    except FileNotFoundError:
        pass


def _expand_kind() -> int:
    import winreg
    return winreg.REG_EXPAND_SZ


def _broadcast() -> None:
    """Tell Explorer the environment changed, so new terminals get the PATH."""
    import ctypes
    HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG = 0xFFFF, 0x001A, 0x0002
    ctypes.windll.user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment",
                                             SMTO_ABORTIFHUNG, 5000, None)


# --------------------------------------------------------------------------- #
# the user's PATH
# --------------------------------------------------------------------------- #

def _same(a: str, b: str) -> bool:
    return ntpath.normcase(ntpath.normpath(a.strip())) == ntpath.normcase(ntpath.normpath(b.strip()))


def user_path() -> list[str]:
    got = _get(ENV_KEY, "Path")
    return [p for p in (got[0] if got else "").split(";") if p.strip()]


def on_user_path(directory: Path) -> bool:
    return any(_same(p, str(directory)) for p in user_path())


def add_to_user_path(directory: Path) -> bool:
    """Append `directory` to the user's PATH; False if it was there already."""
    if on_user_path(directory):
        return False
    got = _get(ENV_KEY, "Path")
    entries = [p for p in (got[0] if got else "").split(";") if p.strip()]
    _set(ENV_KEY, "Path", ";".join([*entries, str(directory)]), got[1] if got else _expand_kind())
    _broadcast()
    return True


def remove_from_user_path(directory: Path) -> bool:
    got = _get(ENV_KEY, "Path")
    if not got:
        return False
    entries = [p for p in got[0].split(";") if p.strip()]
    kept = [p for p in entries if not _same(p, str(directory))]
    if kept == entries:
        return False
    _set(ENV_KEY, "Path", ";".join(kept), got[1])
    _broadcast()
    return True


# --------------------------------------------------------------------------- #
# start at login
# --------------------------------------------------------------------------- #

def run_command() -> str | None:
    got = _get(RUN_KEY, RUN_NAME)
    return got[0] if got else None


def set_run_command(command: str) -> None:
    _set(RUN_KEY, RUN_NAME, command)


def delete_run_command() -> None:
    _delete(RUN_KEY, RUN_NAME)
