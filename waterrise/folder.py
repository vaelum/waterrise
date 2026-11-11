"""A planning folder, read from disk: its settings, its items, and its answers.

An item is read from where it lives, its home (`read`, below): a plan can live
in a worktree under `worktrees/`, on that branch, with its answers beside it.
`Folder.root` stays the main checkout's folder, and its protocols stay there.

Reading never raises on bad content. Everything wrong with a file is collected
as a Problem on it, so `check` can report all of them at once and the service
can still show the rest of a folder while one file is half-written.
"""

from __future__ import annotations

import re
import subprocess
import threading
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import answers as answers_mod
from .types import (ANSWERS_FOLDER, ASSIGNMENTS_FILE, CONFIG_FILE, NOTES_FOLDER, PROTOCOLS_FOLDER, REPORTS_FOLDER, SCHEMA_FOLDER,
                    STATE_FOLDERS, Problem, load, load_config)
from .proc import hidden

_TOML_LINE = re.compile(r"\(at line (\d+), column (\d+)\)")


@dataclass(frozen=True)
class Home:
    """A worktree under `worktrees/` that an item lives in."""
    name: str
    checkout: Path
    planning: Path


_ID_AT = re.compile(r'^\s*id\s*=\s*"([^"]*)"')
_KEY_RUN = re.compile(r"[\w-]*")
_WORD = re.compile(r"\w")


@dataclass
class Item:
    path: Path
    rel: str                      # relative to the planning folder
    folder: str                   # draft | todo | in-progress | done | notes
    text: str = ""
    data: dict = field(default_factory=dict)
    problems: list[Problem] = field(default_factory=list)
    parsed: bool = False
    home: Home | None = None      # the worktree it lives in; None for the main checkout
    _lines: tuple[dict, dict] | None = field(default=None, repr=False, compare=False)

    @property
    def id(self) -> str:
        v = self.data.get("id")
        return v if isinstance(v, str) else ""

    @property
    def kind(self) -> str:
        v = self.data.get("kind")
        return v if isinstance(v, str) else ""

    @property
    def title(self) -> str:
        v = self.data.get("title")
        return v if isinstance(v, str) else self.path.stem

    @property
    def name(self) -> str:
        """The file's name inside its folder, without `.toml`: for humans only."""
        return self.rel.split("/", 1)[1][:-5] if "/" in self.rel else self.rel[:-5]

    # ---- the parts ----------------------------------------------------------- #

    def decisions(self) -> list[dict]:
        """Every decision in the item. A standalone decision is its own only
        decision, with the item's id as its local id."""
        if self.kind == "decision":
            return [self.data]
        return [d for d in self.data.get("decision", []) if isinstance(d, dict)]

    def phases(self) -> list[dict]:
        return [p for p in self.data.get("phase", []) if isinstance(p, dict)]

    def steps(self) -> list[dict]:
        return [s for p in self.phases() for s in p.get("step", []) if isinstance(s, dict)]

    def findings(self) -> list[dict]:
        return [f for f in self.data.get("finding", []) if isinstance(f, dict)]

    def sections(self) -> list[dict]:
        return [s for s in self.data.get("section", []) if isinstance(s, dict)]

    def locals(self) -> dict[str, str]:
        """local id -> what it names (decision, phase, step, finding, section)."""
        out: dict[str, str] = {}
        for d in self.decisions():
            if isinstance(d.get("id"), str):
                out[d["id"]] = "decision"
        for p in self.phases():
            if isinstance(p.get("id"), str):
                out[p["id"]] = "phase"
        for s in self.steps():
            if isinstance(s.get("id"), str):
                out[s["id"]] = "step"
        for f in self.findings():
            if isinstance(f.get("id"), str):
                out[f["id"]] = "finding"
        for s in self.sections():
            if isinstance(s.get("id"), str):
                out[s["id"]] = "section"
        return out

    def find(self, local: str) -> dict | None:
        for group in (self.decisions(), self.phases(), self.steps(), self.findings(), self.sections()):
            for x in group:
                if x.get("id") == local:
                    return x
        return None

    def line_of(self, problem: Problem) -> int | None:
        """Best-effort line for a problem: its own, the line of the local id it
        names, or the line of its top-level key."""
        if problem.line:
            return problem.line
        ids, keys = self._line_index()
        if problem.local and problem.local in ids:
            return ids[problem.local]
        if problem.where:
            return keys.get(re.split(r"[.\[]", problem.where)[0])
        return None

    def _line_index(self) -> tuple[dict, dict]:
        """The first line of each local id (`id = "…"`) and of each key a line
        starts with, after up to two `[`: what `^\\s*id\\s*=\\s*"<id>"` and
        `^\\s*\\[{0,2}<key>\\b` would find, built once per item rather than once
        per problem (p-pw7ft8 s3-1). A key is indexed at every prefix of the
        line's leading run of word characters and `-` that ends on a word
        boundary, as `\\b` would match it."""
        if self._lines is None:
            ids: dict[str, int] = {}
            keys: dict[str, int] = {}
            for i, line in enumerate(self.text.splitlines(), 1):
                m = _ID_AT.match(line)
                if m:
                    ids.setdefault(m.group(1), i)
                s = line.lstrip()
                s = s[2:] if s.startswith("[[") else s[1:] if s.startswith("[") else s
                if "" not in keys and s and _WORD.match(s[0]):
                    keys[""] = i                # an empty key: the first line with a word there
                run = _KEY_RUN.match(s).group()
                for n in range(1, len(run) + 1):
                    after = s[n] if n < len(s) else ""
                    if bool(_WORD.match(run[n - 1])) != bool(after and _WORD.match(after)):
                        keys.setdefault(run[:n], i)
            self._lines = (ids, keys)
        return self._lines


@dataclass
class Folder:
    root: Path                     # the planning folder
    repo: Path                     # the repository it belongs to
    config: dict | None = None     # planning.toml, loaded; None if absent
    config_problems: list[Problem] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    legacy: list[str] = field(default_factory=list)    # Markdown plans, relative
    forms: list[str] = field(default_factory=list)     # the prototype's HTML forms
    stray: list[str] = field(default_factory=list)
    report_files: list[str] = field(default_factory=list)  # reports/ files that are no item
    answer_files: dict[str, Path] = field(default_factory=dict)
    homes: dict[str, Home] = field(default_factory=dict)  # the items that live in a worktree
    assignments: dict[str, dict] = field(default_factory=dict)  # plan id -> its worktree
    assignments_problem: str | None = None
    main_items: dict[str, Item] = field(default_factory=dict)  # what the main checkout's own folder holds
    copies: dict[str, Folder] = field(default_factory=dict)    # each worktree's own folder, read alone

    @property
    def exists(self) -> bool:
        return self.root.is_dir()

    @property
    def typed(self) -> bool:
        return self.config is not None

    @property
    def user(self) -> str:
        return (self.config or {}).get("user") or "the user"

    @property
    def plan_approval(self) -> bool:
        return bool((self.config or {}).get("plan_approval", True))

    def by_id(self) -> dict[str, Item]:
        out: dict[str, Item] = {}
        for it in self.items:
            if it.id and it.id not in out:
                out[it.id] = it
        return out

    def get(self, item_id: str) -> Item | None:
        return self.by_id().get(item_id)

    def answers_path(self, item_id: str) -> Path:
        """An item's answers are beside it, in its home."""
        home = self.homes.get(item_id)
        return (home.planning if home else self.root) / ANSWERS_FOLDER / f"{item_id}.json"

    def answers(self, item_id: str) -> dict:
        return answers_mod.read(self.answers_path(item_id), item_id)

    @property
    def assignments_path(self) -> Path:
        return self.root / ASSIGNMENTS_FILE

    def assignment(self, item_id: str) -> str | None:
        """The name of the worktree a plan is assigned to."""
        name = self.assignments.get(item_id, {}).get("name")
        return name if isinstance(name, str) and name else None

    def worktree_of(self, it: Item) -> str | None:
        """The worktree a plan belongs to: its assignment, or where it lives."""
        return self.assignment(it.id) or (it.home.name if it.home else None)

    def move_target(self, it: Item, folder: str) -> Path:
        """Where `it` lands in `folder`: the same name, under the other folder."""
        return (it.home.planning if it.home else self.root) / folder / it.rel.split("/", 1)[1]

    def move(self, it: Item, folder: str) -> Path:
        """Move `it`'s file to `folder`, with `git mv` when git tracks it.
        Says nothing about whether it may: that is check.folder_rules."""
        target = self.move_target(it, folder)
        if target.exists():
            raise FileExistsError(str(target))
        target.parent.mkdir(parents=True, exist_ok=True)
        repo = it.home.checkout if it.home else self.repo
        if _tracked(repo, it.path):
            subprocess.run(["git", "-C", str(repo), "mv", str(it.path), str(target)],
                           check=True, capture_output=True, timeout=30, stdin=subprocess.DEVNULL, **hidden())
        else:
            it.path.rename(target)
        return target


def _tracked(repo: Path, path: Path) -> bool:
    try:
        return subprocess.run(["git", "-C", str(repo), "ls-files", "--error-unmatch", str(path)],
                              capture_output=True, timeout=5, stdin=subprocess.DEVNULL, **hidden()).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


# What a file's text parses to, keyed on the text: every worktree checks out a
# copy of every plan, and most copies are byte-identical, so the same text is
# parsed once per process (p-pw7ft8 s3-6). The key is the content itself, so an
# entry cannot go stale; the cache is bounded by the size of its texts, so the
# long-running service stays small. Items share the parsed data and problems,
# which nothing changes after parsing.
_PARSED: dict[str, tuple[dict, bool, tuple[Problem, ...]]] = {}
_PARSED_LIMIT = 32 * 1024 * 1024        # characters of text kept as keys
_parsed_size = 0
_parsed_lock = threading.Lock()         # the service reads folders from several threads


def _parse_text(text: str) -> tuple[dict, bool, tuple[Problem, ...]]:
    global _parsed_size
    hit = _PARSED.get(text)
    if hit is not None:
        return hit
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        msg = str(e)
        m = _TOML_LINE.search(msg)
        line = int(m.group(1)) if m else None
        hint = ""
        if "'''" in text:
            hint = " (a prose field in ''' … ''' cannot itself contain three single quotes)"
        out = ({}, False, (Problem("error", f"not valid TOML: {_TOML_LINE.sub('', msg).strip()}{hint}",
                                   line=line),))
    else:
        loaded = load(raw)
        out = (loaded.data, True, tuple(loaded.problems))
    with _parsed_lock:
        if text not in _PARSED:
            while _PARSED and _parsed_size + len(text) > _PARSED_LIMIT:
                old = next(iter(_PARSED))       # the oldest first
                del _PARSED[old]
                _parsed_size -= len(old)
            _PARSED[text] = out
            _parsed_size += len(text)
    return out


def _parse_item(path: Path, root: Path, folder: str, rel: str | None = None) -> Item:
    rel = rel if rel is not None else path.relative_to(root).as_posix()
    it = Item(path=path, rel=rel, folder=folder)
    try:
        it.text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        it.problems.append(Problem("error", f"cannot read: {e}"))
        return it
    it.data, it.parsed, problems = _parse_text(it.text)
    it.problems.extend(problems)
    return it


def worktree_homes(root: Path, repo: Path) -> list[Home]:
    """The worktrees in the project's worktree folder (layout.py) with a copy of
    the planning folder, by name. No git: a linked worktree has a `.git` file. A
    folder that is not in its repository's main checkout (a central project's)
    has none, nor does a repository without the folder."""
    from . import layout
    try:
        rel = root.relative_to(repo)
        folders = sorted(layout.setting(repo, root).path.iterdir())
    except (ValueError, OSError):
        return []
    return [Home(d.name, d, d / rel) for d in folders if (d / ".git").is_file() and (d / rel).is_dir()]


def read(root: Path, repo: Path | None = None) -> Folder:
    """Everything under `root`, a project's planning folder, each item from its
    home. Missing is not an error here."""
    f = _read_one(root, repo)
    if not f.exists:
        return f
    f.assignments, f.assignments_problem = answers_mod.read_assignments(f.assignments_path)
    _move_old_assignments(f)
    _read_homes(f)
    # The user's order inside a folder (dragged in the page), so `list`,
    # `status` and the page all show the folder in the same order. Items the
    # user has not placed follow, by file name.
    f.items.sort(key=lambda it: (it.folder, *_rank(f, it), it.rel))
    return f


def _read_homes(f: Folder) -> None:
    """Replace what lives elsewhere with its copy there. One rule, so nobody has
    to choose between copies: a plan assigned to a worktree that has a copy lives
    in it; anything else in the main checkout has its home there; and what exists
    only under `worktrees/` lives where it is, in the first by name if in several.
    A file that cannot be read has no id: it counts as the file it would replace,
    so a half-written plan in its home is reported, and never answered for by its
    older copy."""
    f.main_items = {it.id: it for it in f.items if it.id}
    homes = worktree_homes(f.root, f.repo)
    for home in homes:
        f.copies[home.name] = _read_one(home.planning, home.checkout)
    homed: dict[str, tuple[Home, Item]] = {}
    unread: list[tuple[Home, Item]] = []
    for home in homes:
        for it in f.copies[home.name].items:
            if it.id and f.assignment(it.id) == home.name:
                homed[it.id] = (home, it)
    for home in homes:
        for it in f.copies[home.name].items:
            if not it.id:
                unread.append((home, it))
            elif it.id not in f.main_items:
                homed.setdefault(it.id, (home, it))
    if not homed and not unread:
        return
    at_rel = {it.rel: it for it in f.items}
    gone: set[str] = set(homed)
    extra: list[Item] = []
    for home, it in unread:
        same = at_rel.get(it.rel)
        if same is None or (same.id and f.assignment(same.id) == home.name):
            it.home = home
            extra.append(it)
            if same is not None:
                gone.add(same.id)
    f.items = [it for it in f.items if it.id not in gone]
    for item_id, (home, it) in homed.items():
        it.home = home
        f.homes[item_id] = home
        extra.append(it)
        answers = f.answers_path(item_id)
        if answers.is_file():
            f.answer_files[item_id] = answers
        else:
            f.answer_files.pop(item_id, None)
    f.items += extra


def _read_one(root: Path, repo: Path | None = None) -> Folder:
    """One planning folder alone, in file order."""
    root = root.resolve()
    f = Folder(root=root, repo=(repo or root.parent).resolve())
    if not root.is_dir():
        return f

    cfg = root / CONFIG_FILE
    if cfg.is_file():
        try:
            loaded = load_config(tomllib.loads(cfg.read_text(encoding="utf-8")))
            f.config = loaded.data
            f.config_problems = loaded.problems
        except (OSError, tomllib.TOMLDecodeError) as e:
            f.config = {}
            f.config_problems = [Problem("error", f"{CONFIG_FILE} is not valid TOML: {e}")]

    prefix = len(str(root)) + 1
    for entry in sorted(root.iterdir()):
        name = entry.name
        if name in (CONFIG_FILE, ASSIGNMENTS_FILE, "README.md", SCHEMA_FOLDER) or name.startswith("."):
            continue
        if name == ANSWERS_FOLDER and entry.is_dir():
            for a in sorted(entry.glob("*.json")):
                f.answer_files[a.stem] = a
            continue
        if name == PROTOCOLS_FOLDER and entry.is_dir():
            continue                    # the steps' protocols; protocol.py reads them
        if entry.is_dir() and name in (*STATE_FOLDERS, NOTES_FOLDER, REPORTS_FOLDER):
            for p in sorted(entry.rglob("*")):
                if not p.is_file() or p.name.startswith("."):
                    continue
                # What rglob under `root` found starts with it: slicing is the
                # same as relative_to, which is slow on many files (p-pw7ft8 s3-6).
                rel = str(p)[prefix:].replace("\\", "/")
                if p.suffix == ".toml":
                    f.items.append(_parse_item(p, root, name, rel))
                elif name == REPORTS_FOLDER:
                    # A report's document, and anything it ships beside it;
                    # check pairs them with their sidecars.
                    f.report_files.append(rel)
                elif p.suffix == ".md":
                    f.legacy.append(rel)
                elif p.suffix == ".html" or p.name.endswith(".answers.json"):
                    f.forms.append(rel)
                else:
                    f.stray.append(rel)
            continue
        f.stray.append(name + ("/" if entry.is_dir() else ""))
    return f


def _move_old_assignments(f: Folder) -> None:
    """An assignment an answers file still holds, from before it had a file of
    its own, moves there the first time it is read."""
    for item_id, path in f.answer_files.items():
        try:
            if '"worktree"' not in path.read_text(encoding="utf-8"):
                continue
        except (OSError, UnicodeDecodeError):
            continue
        entry = answers_mod.read(path, item_id).get("worktree")
        if not isinstance(entry, dict):
            continue
        f.assignments.setdefault(item_id, entry)
        try:
            answers_mod.update_assignments(f.assignments_path, lambda plans, i=item_id, e=entry: plans.setdefault(i, e))
            answers_mod.update(path, item_id, lambda d: d.pop("worktree", None))
        except OSError:
            pass            # a folder that cannot be written is still read right


def rank(ans: dict, folder: str) -> float | None:
    """Where the user put the item in `folder`, or None. A rank given in
    another folder no longer counts: a moved item starts at the end."""
    o = ans.get("order")
    if not isinstance(o, dict) or o.get("folder") != folder:
        return None
    r = o.get("rank")
    return float(r) if isinstance(r, (int, float)) and not isinstance(r, bool) else None


def _rank(f: Folder, it: Item) -> tuple[int, float]:
    if not it.id or it.id not in f.answer_files:
        return (1, 0.0)
    r = rank(f.answers(it.id), it.folder)
    return (1, 0.0) if r is None else (0, r)
