"""The central home: one git repository for every project that plans outside
its own (central-home, amos, 2026-10-18).

    <data>/plans/                            one git repository (dirs.py:
                                             $XDG_DATA_HOME/waterrise/plans/)
      <slug>/planning/                       one project's typed folder

A project's home is in waterrise's registry only (mode-record): nothing in
the project's repository says that it plans here. waterrise creates the
repository on first use and commits a project's folder after every command
that writes it, the page's answers included, so the record of what amos
approved has a history; pushing it somewhere is amos's.
"""

from __future__ import annotations

import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

from . import dirs, lock

HOMES = ("repo", "central")


def root() -> Path:
    return dirs.data() / "plans"


def planning_for(slug: str) -> Path:
    return root() / slug / "planning"


def is_central(entry: dict | None) -> bool:
    return bool(entry) and entry.get("home") == "central"


def git_root(entry: dict) -> Path:
    """The repository git commands run in for a project's planning folder."""
    return root() if is_central(entry) else Path(entry["path"])


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root()), *args], capture_output=True, text=True, encoding="utf-8",
                          timeout=30, stdin=subprocess.DEVNULL)


def ensure() -> Path:
    """The central repository, made on first use."""
    top = root()
    if not (top / ".git").exists():
        top.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(top)], check=True, capture_output=True, timeout=30,
                       stdin=subprocess.DEVNULL)
        (top / "README.md").write_text(
            "# waterrise: the central plans\n\n"
            "One folder per project that plans outside its own repository:\n"
            "`<slug>/planning/`. Written by waterrise, which commits after every\n"
            "change; `waterrise projects` lists the projects.\n", encoding="utf-8", newline="\n")
        commit("", "waterrise: the central plans start here")
    return top


def _identity() -> list[str]:
    """`-c` options for a machine without a git identity, so a commit never fails on it."""
    have = _git("config", "user.email").stdout.strip()
    return [] if have else ["-c", "user.name=waterrise", "-c", "user.email=waterrise@localhost"]


def commit(slug: str, message: str) -> bool:
    """Commit what changed under `<slug>/` (everything, for ""); False if nothing did."""
    if not (root() / ".git").exists():
        return False
    where = slug or "."
    _git("add", "-A", "--", where)
    if _git("diff", "--cached", "--quiet", "--", where).returncode == 0:
        return False
    out = subprocess.run(["git", *_identity(), "-C", str(root()), "commit", "-q", "-m", message, "--", where],
                         capture_output=True, text=True, timeout=30, encoding="utf-8", stdin=subprocess.DEVNULL)
    return out.returncode == 0


# --------------------------------------------------------------------------- #
# moving a project between the homes
# --------------------------------------------------------------------------- #

class Busy(Exception):
    """The service is writing one of the folder's answers files."""


@contextmanager
def _all_answer_locks(planning: Path):
    """Every answers file's lock, taken without waiting, for as long as the
    move runs: one the service holds means it is mid-write, and the move is
    refused rather than racing it."""
    held = []
    try:
        for f in sorted((planning / "answers").glob("*.json")):
            try:
                held.append(lock.acquire(f.parent / f".{f.name}.lock", wait=False))
            except BlockingIOError:
                raise Busy(f"the planning service is writing {f.name}; try again") from None
        yield
    finally:
        for fh in held:
            lock.release(fh)


def _tracked(repo: Path, path: Path) -> bool:
    out = subprocess.run(["git", "-C", str(repo), "ls-files", "--", str(path)],
                         capture_output=True, text=True, timeout=30, encoding="utf-8", stdin=subprocess.DEVNULL)
    return out.returncode == 0 and bool(out.stdout.strip())


def _repo_commit(repo: Path, path: Path, message: str) -> None:
    """Commit `path` in the project's own repository, and nothing else in it."""
    subprocess.run(["git", "-C", str(repo), "add", "-A", "--", str(path)], capture_output=True, timeout=30,
                   stdin=subprocess.DEVNULL)
    if subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--quiet", "--", str(path)],
                      timeout=30, stdin=subprocess.DEVNULL).returncode != 0:
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", message, "--", str(path)],
                       check=True, capture_output=True, timeout=30, stdin=subprocess.DEVNULL)


def move_home(entry: dict, to: str, repo_planning: Path, set_home) -> Path:
    """Carry a project's whole planning folder to the other home.

    `set_home(home, planning)` points the registry at the new place; it runs
    after the copy and before the old folder goes, under the answers' locks,
    so the service never serves a half-moved folder. Ids and answers files
    travel unchanged, so every page URL still opens.
    """
    slug, repo = entry["slug"], Path(entry["path"])
    src = Path(entry["planning"])
    dst = planning_for(slug) if to == "central" else repo_planning
    if not src.is_dir():
        raise FileNotFoundError(f"no planning folder at {src}")
    if dst.exists() and any(dst.iterdir()):
        raise FileExistsError(f"{dst} exists and is not empty")
    if to == "central":
        ensure()
    old = src if to == "central" or src.parent.name != slug else src.parent
    tracked = to == "central" and _tracked(repo, src)
    with _all_answer_locks(src):
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".*.lock"), dirs_exist_ok=True)
        set_home(to, dst)
        _remove_but_held_locks(old)
    # The lock files are released now and go with the folder; the commits come
    # after, so that they record the folder gone, lock files and all.
    shutil.rmtree(old, ignore_errors=True)
    if to == "central":
        if tracked:
            _repo_commit(repo, src, "planning: the plans moved to waterrise's central home")
        commit(slug, f"{slug}: moved in from {repo}")
    else:
        _repo_commit(repo, dst, "planning: the plans back from waterrise's central home")
        commit(slug, f"{slug}: moved back to {repo}")
    return dst


def _remove_but_held_locks(folder: Path) -> None:
    """Everything in `folder` but the lock files we hold: Windows won't delete
    a file that is open (WinError 32), and they are released only after."""
    for path in sorted(folder.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        try:
            if path.is_dir() and not path.is_symlink():
                path.rmdir()
            else:
                path.unlink()
        except OSError:
            if not (path.name.startswith(".") and path.name.endswith(".lock")) and not path.is_dir():
                raise
