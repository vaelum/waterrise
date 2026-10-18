"""The project a command runs against: the harness's Ctx, cut down to what
planning uses.

Where it comes from, first match wins:

    root      $WATERRISE_PROJECT, else the registered project the cwd is in,
              else the git toplevel of the cwd, else the cwd
    planning  a central project's folder in the central repository (its
              home, in the registry); else $WATERRISE_PLANNING_DIR
              (relative to the root), else the registry's entry, else
              <root>/planning
    name      $WATERRISE_PROJECT_NAME, else the registry's entry, else
              butler/butler.toml's [project] name, else the root's name

butler's `planning` command sets all three, so a project on the harness reads
the same folder under the same name as it always did.
"""

from __future__ import annotations

import os
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import ui

PROJECT_ENV = "WATERRISE_PROJECT"
PLANNING_ENV = "WATERRISE_PLANNING_DIR"
NAME_ENV = "WATERRISE_PROJECT_NAME"
PROG_ENV = "WATERRISE_PROG"


def prog() -> str:
    """How the user types this tool: `waterrise`, or what the caller says.

    butler sets it to `python butler.py planning`, so every hint reads as the
    command the user actually ran.
    """
    return os.environ.get(PROG_ENV) or "waterrise"


def short() -> str:
    """The short name messages use between backticks: `planning check` under
    butler, as it always said, and `waterrise check` on its own."""
    return prog().split()[-1]


@dataclass
class Project:
    root: Path
    planning: Path
    name: str
    home: str = "repo"              # "repo" or "central"
    slug: str | None = None         # its registry slug, once registered
    git_root: Path | None = None    # where git runs for the planning folder
    dry_run: bool = False
    verbose: bool = False
    args: object | None = field(default=None, repr=False)

    def disp(self, path: Path | str) -> str:
        """Path for display: relative inside the project, absolute outside it."""
        p = Path(path)
        try:
            return str(p.resolve().relative_to(self.root.resolve()))
        except ValueError:
            return str(p)

    @property
    def registered(self) -> bool:
        return self.slug is not None

    def would(self, description: str) -> bool:
        """Guard a change for --dry-run: print it and say so instead."""
        if self.dry_run:
            ui.plain(ui.dim(f"[dry-run] would {description}"))
        return self.dry_run


def _git_toplevel(cwd: Path) -> Path | None:
    try:
        out = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None


def _registered(cwd: Path, entries: list[dict]) -> dict | None:
    """The deepest registered project that contains `cwd`."""
    best: tuple[int, dict] | None = None
    for e in entries:
        p = Path(e["path"])
        if cwd == p or p in cwd.parents:
            depth = len(p.parts)
            if best is None or depth > best[0]:
                best = (depth, e)
    return best[1] if best else None


def _butler_name(root: Path) -> str | None:
    for path in (root / "butler" / "butler.toml", root / "butler.toml"):
        try:
            data = tomllib.loads(path.read_text())
        except (OSError, tomllib.TOMLDecodeError):
            continue
        name = (data.get("project") or {}).get("name")
        if isinstance(name, str) and name:
            return name
    return None


def resolve(cwd: Path | None = None) -> Project:
    from . import central
    from .client import registry

    cwd = (cwd or Path.cwd()).resolve()
    entries = registry()
    env_root = os.environ.get(PROJECT_ENV)
    if env_root:
        root = Path(env_root).resolve()
        entry = next((e for e in entries if Path(e["path"]) == root), None)
    else:
        entry = _registered(cwd, entries)
        root = Path(entry["path"]) if entry else (_git_toplevel(cwd) or cwd)
    env_dir = os.environ.get(PLANNING_ENV)
    if central.is_central(entry):
        planning = Path(entry["planning"])
    elif env_dir:
        planning = root / env_dir
    elif entry and entry.get("planning"):
        planning = Path(entry["planning"])
    else:
        planning = root / "planning"
    name = (os.environ.get(NAME_ENV) or (entry or {}).get("name") or _butler_name(root)
            or root.name)
    return Project(root=root, planning=planning, name=name,
                   home=(entry or {}).get("home") or "repo", slug=(entry or {}).get("slug"),
                   git_root=central.root() if central.is_central(entry) else root)
