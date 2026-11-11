"""A project's worktrees: `<its worktree folder>/<name>`, `worktrees/` unless the
project sets another (layout.py).

Git knows every worktree of a repository; waterrise names and assigns only the
main checkout (`main`) and the ones in the project's worktree folder. The
others, which other tools make where they like, resolve to the project but are
shown only on request and cannot be assigned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import answers as answers_mod
from . import layout
from .context import Project, _git, _main_checkout, prog
from .errors import WaterriseError
from .folder import Folder, Item
from .types import WORKTREES_FOLDER as FOLDER

MAIN = "main"
OPEN_FOLDERS = ("todo", "in-progress")
IGNORE_RULE = f"/{FOLDER}/"     # the default's; ignore_rule(root) gives a project's


def folder_of(root: Path) -> layout.Setting:
    """The project's worktree folder, and where it is set."""
    return layout.setting(root)


def ignore_rule(root: Path) -> str | None:
    """The line for the top .gitignore; None for a folder outside the checkout."""
    s = folder_of(root)
    return f"/{s.rel}/" if s.inside else None


@dataclass
class Worktree:
    name: str
    path: Path
    branch: str | None          # None when detached
    head: str = ""
    main: bool = False
    conventional: bool = False  # in `worktrees/`

    @property
    def assignable(self) -> bool:
        return self.main or self.conventional


def read(root: Path) -> list[Worktree]:
    """Every worktree of the repository whose folder exists, the main one first."""
    out = _git(root, "worktree", "list", "--porcelain")
    folder = folder_of(root).path
    found: list[Worktree] = []
    for block in (out or "").split("\n\n"):
        lines = block.splitlines()
        if not lines or not lines[0].startswith("worktree ") or "bare" in lines:
            continue
        path = Path(lines[0][len("worktree "):]).resolve()
        if not path.is_dir():
            continue
        branch = next((x[len("branch refs/heads/"):] for x in lines if x.startswith("branch refs/heads/")), None)
        head = next((x[len("HEAD "):] for x in lines if x.startswith("HEAD ")), "")
        main = path == root
        conventional = path.parent == folder
        found.append(Worktree(MAIN if main else path.name, path, branch, head, main, conventional))
    return found


def get(root: Path, name: str) -> Worktree | None:
    """A worktree that can be named: the main checkout or one in `worktrees/`."""
    return next((w for w in read(root) if w.assignable and w.name == name), None)


def count(root: Path) -> int:
    """How many worktrees the folder holds, without asking git: this is for the
    index of every repository. A linked worktree has a `.git` file."""
    try:
        return sum(1 for d in folder_of(root).path.iterdir() if (d / ".git").is_file())
    except OSError:
        return 0


def here(ctx: Project) -> Worktree | None:
    """The worktree a command runs in, if it is one that can be named."""
    return next((w for w in read(ctx.root) if w.assignable and w.path == ctx.checkout), None)


def state(root: Path, w: Worktree, main_head: str | None = None) -> dict:
    """Clean or not (None: git did not answer in time), and how far from the
    main checkout's HEAD. `main_head` saves asking for it again."""
    status = _git(w.path, "status", "--porcelain")
    if main_head is None:
        main_head = (_git(root, "rev-parse", "HEAD") or "").strip()
    counts = (_git(w.path, "rev-list", "--left-right", "--count", f"{main_head}...HEAD") or "").split() \
        if main_head and not w.main else []
    return {"clean": status == "" if status is not None else None,
            "behind": int(counts[0]) if len(counts) == 2 else 0,
            "ahead": int(counts[1]) if len(counts) == 2 else 0}


POOL = 8        # worktrees asked at once; each git call keeps _git's own timeout


def states(root: Path, trees: list[Worktree]) -> dict[Path, dict]:
    """`state` of each worktree, by path: the main checkout's HEAD from what
    `git worktree list` already said, and the worktrees in a small pool, so many
    of them cost about as long as the slowest few (p-pw7ft8 s3-2)."""
    if not trees:
        return {}
    main = next((w for w in read(root) if w.main), None) if not any(w.main for w in trees) else \
        next(w for w in trees if w.main)
    main_head = main.head if main and main.head else (_git(root, "rev-parse", "HEAD") or "").strip()
    if len(trees) == 1:
        return {trees[0].path: state(root, trees[0], main_head)}
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(POOL, len(trees))) as pool:
        found = list(pool.map(lambda w: state(root, w, main_head), trees))
    return {w.path: s for w, s in zip(trees, found)}


# ---- plans ---------------------------------------------------------------------- #

def plans_of(f: Folder, name: str, folders: tuple[str, ...] | None = None) -> list[Item]:
    return [it for it in f.items
            if it.parsed and it.kind == "plan" and it.id and (folders is None or it.folder in folders)
            and f.assignment(it.id) == name]


def where(root: Path, name: str | None, known: list[Worktree] | None = None) -> dict | None:
    """A plan's assignment as every reader shows it: name, path, and whether it
    is gone. The path of a gone one is where it would be. `known` is what
    `read` returned, for a caller asking about many plans."""
    if name is None:
        return None
    w = (next((x for x in known if x.assignable and x.name == name), None) if known is not None
         else get(root, name))
    path = w.path if w else (root if name == MAIN else folder_of(root).path / name)
    return {"name": name, "path": str(path), "gone": w is None}


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def differs(f: Folder, it: Item, name: str | None) -> str | None:
    """What changes for the reader when a plan's home moves to `name`: another
    copy's text and answers count from then on, so answers given in this one no
    longer show. Said, not refused: git is what brings the copies together."""
    main = name is None or name == MAIN
    other = f.main_items.get(it.id) if main else (f.copies[name].get(it.id) if name in f.copies else None)
    if other is None or other.path == it.path:
        return None
    theirs = (other.home.planning if other.home else other.path.parents[1]) / "answers" / f"{it.id}.json"
    if other.text == it.text and _text(theirs) == _text(f.answers_path(it.id)):
        return None
    return (f"{other.path} differs from the copy read until now, {it.path}, and counts from now on; "
            "what was answered in the other shows again once it is committed and merged")


def assign(root: Path, f: Folder, it: Item, name: str | None, by: str) -> str | None:
    """Assign a plan to a named worktree, or with None to none. The one writer
    of the assignments file, for the command and the page; what it refuses, it
    says why. What the user should know about the copies, it returns."""
    if it.kind != "plan":
        raise WaterriseError(f"{it.id} is a {it.kind}; only a plan can be assigned to a worktree")
    if name is not None and get(root, name) is None:
        names = [w.name for w in read(root) if w.assignable]
        outside = any(w.name == name for w in read(root))
        raise WaterriseError(f"{name} is outside {layout.where_set(folder_of(root))}, so no plan can be assigned to it"
                             if outside
                             else f"no worktree {name} in this project",
                             hint="there are: " + ", ".join(names) if names else None)

    def change(plans: dict) -> None:
        if name is None:
            plans.pop(it.id, None)
        else:
            plans[it.id] = {"name": name, "at": answers_mod.now(), "by": by}
    warning = differs(f, it, name)
    f.assignments = answers_mod.update_assignments(f.assignments_path, change)
    return warning


def placed(ctx: Project, it: Item, assigned: str | None) -> str | None:
    """Working on an assigned plan from here: None if fine, a warning to print
    if the work may go on, and an error if it should not (decision `enforce`).
    A gone worktree cannot be worked in, so it does not stop anyone."""
    at = where(ctx.root, assigned)
    if at is None or ctx.checkout is None or ctx.checkout == Path(at["path"]):
        return None
    if at["gone"]:
        return (f"{it.id} is assigned to {at['name']}, which no longer exists: "
                f"{prog()} assign {it.id} <worktree>, or --none")
    message = f"{it.id} is assigned to {at['name']}, at {at['path']}"
    if ctx.checkout == ctx.root:
        return f"{message}; this is the main checkout"
    raise WaterriseError(f"{message}, and this is {ctx.checkout}",
                         hint=f"work there, or {prog()} assign {it.id} <worktree>")


# ---- the ignore rule ------------------------------------------------------------ #

def ignored(root: Path) -> bool | None:
    """Does git ignore the folder? None outside a repository, and for a folder
    outside the checkout, which git does not see."""
    s = folder_of(root)
    if not s.inside or _git(root, "rev-parse", "--is-inside-work-tree") is None:
        return None
    return _git(root, "check-ignore", "-q", f"{s.rel}/x") is not None


def ensure_ignored(ctx: Project) -> Path | None:
    """Write the rule unless the folder is ignored already; the file it went in.

    A central project's repository must not show that it plans with waterrise,
    so its rule goes in `.git/info/exclude`, which git does not track."""
    if ignored(ctx.root) is not False:
        return None
    rule = ignore_rule(ctx.root)
    if ctx.home == "central":
        rel = (_git(ctx.root, "rev-parse", "--git-path", "info/exclude") or "").strip()
        path = (ctx.root / rel).resolve()
    else:
        path = ctx.root / ".gitignore"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text + rule + "\n", encoding="utf-8", newline="\n")
    return path


# ---- add and remove ------------------------------------------------------------- #

def _checked(cwd: Path, *args: str) -> str:
    import subprocess

    from .proc import hidden
    out = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=300,
                         encoding="utf-8", stdin=subprocess.DEVNULL, **hidden())
    if out.returncode != 0:
        raise WaterriseError(f"git {args[0]} failed: {(out.stderr or out.stdout).strip()}")
    return out.stdout


def _usable(ctx: Project) -> None:
    if _git(ctx.root, "rev-parse", "--is-inside-work-tree") is None:
        raise WaterriseError(f"{ctx.root} is not a git repository")
    if _main_checkout(ctx.root) is None:
        raise WaterriseError(f"{ctx.root} belongs to a bare repository, which has no main checkout "
                             f"to hold a worktree folder")


def check_name(name: str) -> None:
    if name == MAIN:
        raise WaterriseError(f"`{MAIN}` means the main checkout; choose another name")
    if re.search(r"[/\\]", name) or name in (".", "..") or name.startswith("-") or not name.strip():
        raise WaterriseError(f"{name!r} is not a worktree's name: one folder name, with no path separator")


def add(ctx: Project, name: str, branch: str | None = None, start: str | None = None) -> tuple[Worktree, bool]:
    """Make `worktrees/<name>`. The worktree, and whether its branch is new."""
    _usable(ctx)
    check_name(name)
    path = folder_of(ctx.root).path / name
    if path.exists():
        raise WaterriseError(f"{path} exists already")
    branch = branch or name
    exists = _git(ctx.root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}") is not None
    if exists and start:
        raise WaterriseError(f"the branch {branch} exists, so --from has nothing to do",
                             hint=f"{prog()} worktree add {name} --branch <new branch> --from {start}")
    if exists:
        _checked(ctx.root, "worktree", "add", "--quiet", str(path), branch)
    else:
        _checked(ctx.root, "worktree", "add", "--quiet", "-b", branch, str(path), start or "HEAD")
    return Worktree(name, path, branch, main=False, conventional=True), not exists


def remove(ctx: Project, name: str, f: Folder | None) -> Worktree:
    """`git worktree remove`, which refuses a dirty one. Never forced; the
    branch stays."""
    _usable(ctx)
    if name == MAIN:
        raise WaterriseError(f"`{MAIN}` is the main checkout; it cannot be removed")
    w = get(ctx.root, name)
    if w is None:
        names = [x.name for x in read(ctx.root) if x.conventional]
        raise WaterriseError(f"no worktree {name} in {layout.where_set(folder_of(ctx.root))}",
                             hint=("there are: " + ", ".join(names)) if names else None)
    held = plans_of(f, name, OPEN_FOLDERS) if f is not None and f.exists else []
    if held:
        raise WaterriseError(f"{name} holds {', '.join(f'{p.id} ({p.title})' for p in held)}, still open",
                             hint=f"{prog()} assign {held[0].id} --none, or to another worktree, first")
    only = unmerged(ctx.root, f, w) if f is not None and f.exists else []
    if only:
        raise WaterriseError(f"{name} holds {', '.join(f'{p.id} ({p.title})' for p in only)} on {w.branch or 'its HEAD'}, "
                             "not merged, so the plan would go from the page or back to an older copy",
                             hint=f"merge {w.branch or w.head} into the main checkout first")
    _checked(ctx.root, "worktree", "remove", str(w.path))
    return w


def unmerged(root: Path, f: Folder, w: Worktree) -> list[Item]:
    """The plans living in `w` whose current copy only its branch has."""
    if not w.head or _git(root, "merge-base", "--is-ancestor", w.head, "HEAD") is not None:
        return []
    return [it for it in f.items
            if it.home and it.home.checkout == w.path and it.kind == "plan"
            and (it.id not in f.main_items or f.main_items[it.id].text != it.text)]
