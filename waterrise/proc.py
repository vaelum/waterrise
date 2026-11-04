"""Starting other programs without a window flashing up on Windows.

The service runs under pythonw.exe, which has no console. A console program
it starts, git above all, then gets a console window of its own, which
opens and closes on every page view. `hidden()` asks Windows for none; it is
for children whose output is captured or discarded. A child that reads the
user's terminal or prints into it (pip in `install`, the command of
`waterrise run`) keeps its parent's console and takes no flag.
"""

from __future__ import annotations

import os

CREATE_NO_WINDOW = 0x08000000      # subprocess has the name on Windows only
# The real platform, not dirs.WINDOWS: tests make waterrise use Windows' paths
# on Linux, where a child with creationflags is refused.
WINDOWS = os.name == "nt"


def hidden() -> dict:
    """Popen keyword arguments for a child with no console window on Windows;
    nothing elsewhere (POSIX refuses creationflags)."""
    return {"creationflags": CREATE_NO_WINDOW} if WINDOWS else {}
