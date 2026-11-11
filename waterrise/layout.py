"""Where a project's worktrees live: one folder, relative to its main checkout.

`worktrees/` unless the project's `planning.toml` sets `worktrees`, and this
machine's registry entry for the project overrides both (`waterrise projects
worktrees`). The value is a path relative to the main checkout, `/` separated on
every system, and may go up (`../proj-wt`): the same file is read in a clone
elsewhere and on Windows, so an absolute path or `~` is refused.

Everything that names a worktree, finds a plan's home or writes the ignore rule
asks `setting(root)`. It is read on every call, cheaply: the registry and the
planning.toml are small, and the answer is kept until either file changes.
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from .types import CONFIG_FILE, WORKTREES_FOLDER

KEY = "worktrees"
SOURCES = ("default", "planning.toml", "registry")


@dataclass(frozen=True)
class Setting:
    rel: str                 # as written, normalised to `/`
    path: Path               # resolved: where the folder is, or would be
    source: str              # one of SOURCES
    problem: str | None = None   # an invalid value, set where `source` says; the default is used instead

    @property
    def inside(self) -> bool:
        """Inside the main checkout, so git would see it without an ignore rule."""
        return not self.rel.split("/")[0] == ".."

    def name(self, name: str) -> Path:
        return self.path / name


def invalid(value: object) -> str | None:
    """Why `value` cannot be a project's worktree folder, or None."""
    if not isinstance(value, str):
        return f"must be a string, a path relative to the main checkout, not {type(value).__name__}"
    v = value.strip()
    if not v:
        return "is empty; leave it out for the default, worktrees"
    if v.startswith("~"):
        return "starts with ~; give a path relative to the main checkout"
    if PurePosixPath(v).is_absolute() or PureWindowsPath(v).is_absolute() or PureWindowsPath(v).drive:
        return "is absolute; give a path relative to the main checkout, so it holds in a clone and on Windows"
    if "\\" in v:
        return "uses \\; separate with / on every system"
    parts = [p for p in v.split("/") if p not in ("", ".")]
    if not parts:
        return "is the main checkout itself"
    if parts[0] in (".git",):
        return "is inside .git"
    return None


def _norm(value: str) -> str:
    parts = [p for p in value.strip().split("/") if p not in ("", ".")]
    return "/".join(parts)


_memo: dict[tuple, Setting] = {}


def _stamp(path: Path) -> tuple:
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return ()


def _registry_path() -> Path:
    from .client import REGISTRY, state_dir
    return state_dir() / REGISTRY


_entries: dict[tuple, dict | None] = {}


def _entry(root: Path, registry: Path, stamp: tuple) -> dict | None:
    key = (root, str(registry), stamp)
    if key not in _entries:
        if len(_entries) > 256:
            _entries.clear()
        _entries[key] = _read_entry(root, registry)
    return _entries[key]


def _read_entry(root: Path, registry: Path) -> dict | None:
    try:
        rows = json.loads(registry.read_text(encoding="utf-8")).get("repos", [])
    except (OSError, ValueError, AttributeError):
        return None
    for r in rows:
        try:
            if isinstance(r, dict) and Path(r["path"]).resolve() == root:
                return r
        except (KeyError, OSError, TypeError):
            continue
    return None


def _from_toml(planning: Path) -> object:
    try:
        return tomllib.loads((planning / CONFIG_FILE).read_text(encoding="utf-8")).get(KEY)
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return None


def setting(root: Path, planning: Path | None = None) -> Setting:
    """The worktree folder of the project whose main checkout is `root`.
    `planning` is its planning folder when known (a central project's is
    elsewhere); otherwise the registry's, or `<root>/planning`."""
    root = Path(root).resolve()
    registry = _registry_path()
    reg_stamp = _stamp(registry)
    entry = _entry(root, registry, reg_stamp) if reg_stamp else None
    if planning is None:
        planning = Path(entry["planning"]) if entry and entry.get("planning") else root / "planning"
    key = (root, str(planning), str(registry), reg_stamp, _stamp(planning / CONFIG_FILE), os.name)
    hit = _memo.get(key)
    if hit is not None:
        return hit
    problem = None
    out = None
    for source, value in (("registry", (entry or {}).get(KEY)), ("planning.toml", _from_toml(planning))):
        if value is None:
            continue
        why = invalid(value)
        if why:
            problem = problem or f"{KEY} = {value!r} in {source} {why}"
            continue
        rel = _norm(value)
        out = Setting(rel, (root / rel).resolve(), source, problem)
        break
    if out is None:
        out = Setting(WORKTREES_FOLDER, (root / WORKTREES_FOLDER).resolve(), "default", problem)
    if len(_memo) > 256:
        _memo.clear()
    _memo[key] = out
    return out


def where_set(s: Setting) -> str:
    """For messages: `.worktrees/ (planning.toml)`."""
    return f"{s.rel}/ ({'the default' if s.source == 'default' else 'this machine' if s.source == 'registry' else s.source})"
