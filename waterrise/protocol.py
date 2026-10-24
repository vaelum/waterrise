"""A step's protocol: what the agent noted and what its commands printed.

    planning/protocols/<plan id>/<step>.md

One Markdown file per step, appended to only, never committed (`where`,
amos 2026-10-24: the planning folder's .gitignore names `protocols/`). Each
entry starts with a heading that dates it:

    ## 2026-10-24 14:02:11 note
    Starting s2-1: …

    ## 2026-10-24 14:02:15 run
    ~~~~~~
    $ python -m pytest -q
    …the output, as it came…
    ~~~~~~
    exit 1 after 3.4 s

A file stops growing at 5 MB (`size`, amos 2026-10-24): past that, notes are
still written in full, and a command keeps only its last 50 lines. Before
anything is written, the values in ~/.secrets and in credential-shaped
environment variables are replaced by `<redacted NAME>`.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

from . import lock
from .types import PROTOCOLS_FOLDER

FOLDER = PROTOCOLS_FOLDER
IGNORE_LINE = f"{FOLDER}/"
CAP = 5 * 1024 * 1024          # bytes a protocol file may reach before commands are cut
TAIL = 50                      # lines a command keeps once the file is past CAP
FENCE = "~" * 6                # not ```: a command's own output often holds those
CREDENTIAL_ENV = re.compile(r"(TOKEN|KEY|PASSWORD|PASSWD|SECRET)", re.I)
MIN_SECRET = 6                 # shorter values would mask ordinary words


def path_for(planning: Path, plan_id: str, step: str) -> Path:
    return planning / FOLDER / plan_id / f"{step}.md"


def ensure_ignored(planning: Path) -> None:
    """Protocols stay out of git: `protocols/` in the planning folder's
    .gitignore, added to one that predates it."""
    ignore = planning / ".gitignore"
    try:
        lines = ignore.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    if IGNORE_LINE not in lines:
        lines.append(IGNORE_LINE)
        ignore.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


# --------------------------------------------------------------------------- #
# masking
# --------------------------------------------------------------------------- #

def _secrets_file() -> Path:
    return Path.home() / ".secrets"


def secrets_to_mask() -> list[tuple[str, str]]:
    """(value, name) pairs, longest value first so one never masks part of another."""
    found: dict[str, str] = {}
    try:
        for line in _secrets_file().read_text(encoding="utf-8", errors="replace").splitlines():
            name, sep, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            if sep and name.strip() and len(value) >= MIN_SECRET:
                found.setdefault(value, name.strip())
    except OSError:
        pass
    for name, value in os.environ.items():
        if CREDENTIAL_ENV.search(name) and len(value) >= MIN_SECRET:
            found.setdefault(value, name)
    return sorted(found.items(), key=lambda kv: -len(kv[0]))


def mask(text: str, secrets: list[tuple[str, str]]) -> str:
    for value, name in secrets:
        if value in text:
            text = text.replace(value, f"<redacted {name}>")
    return text


# --------------------------------------------------------------------------- #
# writing
# --------------------------------------------------------------------------- #

def _stamp() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Protocol:
    """Appends to one step's protocol, one writer at a time across processes."""

    def __init__(self, planning: Path, plan_id: str, step: str):
        self.path = path_for(planning, plan_id, step)
        self.secrets = secrets_to_mask()
        ensure_ignored(planning)

    def _append(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with lock.held(self.path.parent / f".{self.path.name}.lock"):
            with open(self.path, "a", encoding="utf-8", newline="\n") as f:
                f.write(mask(text, self.secrets))

    def size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def note(self, text: str) -> None:
        self._append(f"\n## {_stamp()} note\n{text.rstrip()}\n")

    def run(self, argv: list[str], echo=None) -> int:
        """Run `argv`, its output to `echo` (default: this process's stdout) and
        to the protocol as it comes; return its exit status."""
        echo = echo or sys.stdout
        # The command line inside the fence, not in the heading: read as
        # Markdown, its quotes and backticks would turn into something else.
        line = subprocess.list2cmdline(argv) if os.name == "nt" else _shell(argv)
        self._append(f"\n## {_stamp()} run\n{FENCE}\n$ {line}\n")
        start, cut, tail = time.monotonic(), 0, deque(maxlen=TAIL)
        try:
            # stdin passed through: the command may ask, and waterrise itself
            # never reads it while the command runs.
            proc = subprocess.Popen(argv, stdin=None, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    env=_child_env())
        except OSError as e:
            self._append(f"{FENCE}\ncould not start: {e}\n")
            print(f"could not start {argv[0]}: {e}", file=sys.stderr)
            return 127
        for raw in iter(proc.stdout.readline, b""):
            line = _decode(raw)
            if line.endswith("\r\n"):         # Windows programs; the protocol is LF everywhere
                line = line[:-2] + "\n"
            if echo is not None:            # None under pythonw
                echo.write(line)
                echo.flush()
            if cut or self.size() + len(raw) > CAP:
                cut += 1
                tail.append(line)
            else:
                self._append(line)
        rc = proc.wait()
        if cut:
            kept = len(tail)
            body = "".join(tail)
            self._append(f"\n… {cut - kept} lines left out: the protocol is over {CAP // (1024 * 1024)} MB …\n"
                         f"{body}")
        if not body_ends_with_newline(self.path):
            self._append("\n")
        self._append(f"{FENCE}\nexit {rc} after {time.monotonic() - start:.1f} s\n")
        return rc


def _child_env() -> dict:
    """A Python child writes its output as UTF-8 (before 3.15 Windows makes a
    pipe cp1252), unless the environment already chooses."""
    env = dict(os.environ)
    if "PYTHONIOENCODING" not in env:
        env.setdefault("PYTHONUTF8", "1")
    return env


def _decode(raw: bytes) -> str:
    """A line of output: UTF-8 if it is, else, on Windows, the console's OEM
    code page that cmd and PowerShell write pipes in."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        if os.name == "nt":
            try:
                return raw.decode("oem")
            except (UnicodeDecodeError, LookupError):
                pass
        return raw.decode("utf-8", errors="replace")


def body_ends_with_newline(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            return f.read(1) == b"\n"
    except OSError:
        return True


def _shell(argv: list[str]) -> str:
    import shlex
    return shlex.join(argv)


# --------------------------------------------------------------------------- #
# reading, for the page
# --------------------------------------------------------------------------- #

HEADING = re.compile(r"^## (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (note|run)$", re.M)


def summary(path: Path) -> dict | None:
    """How many entries a protocol has, when the last one was, and its last
    note's first lines: what the page shows before it is opened."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    heads = list(HEADING.finditer(text))
    if not heads:
        return {"entries": 0, "last": None, "last_note": None}
    last_note = None
    for i in range(len(heads) - 1, -1, -1):
        if heads[i].group(2) == "note":
            end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            last_note = text[heads[i].end():end].strip()[:300] or None
            break
    return {"entries": len(heads), "last": heads[-1].group(1), "last_note": last_note}


def summaries(planning: Path, plan_id: str) -> dict[str, dict]:
    base = planning / FOLDER / plan_id
    if not base.is_dir():
        return {}
    out = {}
    for p in sorted(base.glob("*.md")):
        s = summary(p)
        if s is not None:
            out[p.stem] = s
    return out
