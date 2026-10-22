"""Where waterrise keeps its own files.

              POSIX                                  Windows
    data      $XDG_DATA_HOME/waterrise               %LOCALAPPDATA%\\waterrise
              (~/.local/share/waterrise)
    state     $XDG_STATE_HOME/waterrise              %LOCALAPPDATA%\\waterrise\\state
              (~/.local/state/waterrise)

The XDG variables override the defaults on both. WATERRISE_STATE_DIR is the
service's own: it runs on the state directory it was started with, and its
children with it.
"""

from __future__ import annotations

import os
from pathlib import Path

WINDOWS = os.name == "nt"
STATE_ENV = "WATERRISE_STATE_DIR"


def _local_appdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")


def data() -> Path:
    if os.environ.get("XDG_DATA_HOME"):
        return Path(os.environ["XDG_DATA_HOME"]) / "waterrise"
    if WINDOWS:
        return _local_appdata() / "waterrise"
    return Path.home() / ".local" / "share" / "waterrise"


def state_home() -> Path:
    """$XDG_STATE_HOME or its default: where butler kept its registry too."""
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")


def state() -> Path:
    if os.environ.get(STATE_ENV):
        return Path(os.environ[STATE_ENV])
    if os.environ.get("XDG_STATE_HOME"):
        return Path(os.environ["XDG_STATE_HOME"]) / "waterrise"
    if WINDOWS:
        return _local_appdata() / "waterrise" / "state"
    return Path.home() / ".local" / "state" / "waterrise"
