"""Terminal output.

Copied from the butler harness, so `python butler.py planning …` and
`waterrise …` look the same: green for what happened, yellow for "you probably
want to know", red for failure.

Colour is disabled when stdout isn't a tty, when NO_COLOR is set (the informal
standard), or via --no-color.
"""

from __future__ import annotations

import os
import sys

_RESET = "\033[0m"
_BOLD_GREEN = "\033[1;32m"
_BOLD_YELLOW = "\033[1;33m"
_BOLD_RED = "\033[1;31m"
_DIM = "\033[2m"
_BOLD = "\033[1m"

_color: bool | None = None


def init(no_color: bool = False) -> None:
    """Decide once, at startup, whether anything is coloured, and make sure
    nothing printed can fail or be misread. Into a pipe or a file, UTF-8,
    unless PYTHONIOENCODING says otherwise: what reads `status --json` reads
    UTF-8, and before Python 3.15 Windows would write cp1252 there. On a
    console that isn't UTF-8, a ✓ becomes a ? instead of a UnicodeEncodeError."""
    global _color
    for stream in (sys.stdout, sys.stderr):
        if not hasattr(stream, "reconfigure"):
            continue
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if encoding == "utf8":
            continue
        try:
            tty = stream.isatty()
        except (OSError, ValueError):
            tty = False
        if not tty and not os.environ.get("PYTHONIOENCODING"):
            stream.reconfigure(encoding="utf-8")
        else:
            stream.reconfigure(errors="replace")
    _color = not (
        no_color
        or os.environ.get("NO_COLOR") is not None
        or sys.stdout is None             # pythonw.exe has none
        or not sys.stdout.isatty()
    )


def _enabled() -> bool:
    if _color is None:
        init()
    return bool(_color)


def _wrap(code: str, text: str) -> str:
    return f"{code}{text}{_RESET}" if _enabled() else text


def bold(text: str) -> str:
    return _wrap(_BOLD, text)


def dim(text: str) -> str:
    return _wrap(_DIM, text)


def ok(label: str, rest: str = "") -> None:
    print(f"{_wrap(_BOLD_GREEN, label)}{' ' + rest if rest else ''}")


def warn(label: str, rest: str = "") -> None:
    print(f"{_wrap(_BOLD_YELLOW, label)}{' ' + rest if rest else ''}")


def err(message: str, hint: str | None = None) -> None:
    print(f"{_wrap(_BOLD_RED, 'error:')} {message}", file=sys.stderr)
    if hint:
        for line in hint.splitlines():
            print(f"       {line}", file=sys.stderr)


def note(message: str) -> None:
    warn("note:", message)


def plain(message: str = "") -> None:
    print(message)
