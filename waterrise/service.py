"""The planning service: one process per user, every registered repository.

Started detached by `planning serve` (client.py), never by hand:

    python -P -c <boot> <src> <port> <state dir>

Pages (one shell, rendered by app.js):

    /                      every repository, and what waits on the user
    /r/<slug>/             one repository's items by folder
    /r/<slug>/i/<id>       one item, interactive
    /r/<slug>/m/<path>     a Markdown file under planning/, read-only
    /r/<slug>/f/<path>     any file under planning/, as is (sandboxed)
    /r/<slug>/<id>/report.html
                           a report's document, for the frame on its page: its
                           own scripts run, with an opaque origin and nothing
                           remote, so it cannot reach this service or the token

API (JSON):

    GET    /api/health
    GET    /api/events?scope=index|repo&slug=<slug>   Server-Sent Events: `hello`
                                          (the version) and `change`, whenever a
                                          file behind that page changes
    GET    /api/events?scope=all          one stream for every page: `change`
                                          carries {index, slugs}, the repositories
                                          whose files changed. The pages share it
                                          through a SharedWorker (events.js), since
                                          a browser opens six connections per host
                                          and a stream per tab would use them up
    POST   /api/shutdown                          token
    GET    /api/repos
    POST   /api/repos                             token: register {path, planning, name, home?};
                                                  a central project's planning is placed
                                                  in the central repository
    PUT    /api/repos/<slug>/home                 token: {home, planning}, after `projects home`
                                                  has carried the folder there
    DELETE /api/repos/<slug>                      token, or the header if the path is gone
    GET    /api/r/<slug>                          the repository's items, in lane order,
                                          with what each plan needs and is needed by
    GET    /api/r/<slug>/item/<id>                one item, its answers and its state
    GET    /api/r/<slug>/file/<path>              a file's text
    PUT    /api/r/<slug>/decision/<id>/<local>    {selected, text}
    PUT    /api/r/<slug>/step/<id>/<local>        {state: "done" | "open"}  (user steps)
    PUT    /api/r/<slug>/approval/<id>/<local>    {verdict, note}           (gated steps)
    PUT    /api/r/<slug>/approval/<id>            {verdict, note}           (the plan)
    POST   /api/r/<slug>/comment/<id>             {on, text}
    PUT    /api/r/<slug>/worktree/<id>            {name}: assign a plan to a worktree;
                                          null for none
    PUT    /api/r/<slug>/order/<folder>           {ids}: the folder's items, in the
                                          order the user dragged them to
    PUT    /api/r/<slug>/move/<id>                {folder, ids?}: move the item to
                                          another lane if that folder's rules allow
                                          it (409 and why, if not); ids is the new
                                          lane's order with it

Who may do what:

- It binds 127.0.0.1 only, and the Host header must name it (DNS rebinding).
- Every write needs the header X-Waterrise and, if the browser sends an
  Origin, this server's own. A web page elsewhere cannot send a custom header
  without a CORS preflight, and none is ever answered.
- The control API (register, forget, shutdown) needs the token from the 0600
  file, so another account on the machine cannot make the service read one of
  this user's directories.
- Pages carry a Content-Security-Policy without inline script; files under /f/
  are sandboxed. Only files under a registered planning folder are served.
- A body is at most 256 KiB; answers are written atomically, one entry at a time.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import mimetypes
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import answers as answers_mod
from . import central, context, dirs, layout, lock
from . import check as check_mod
from . import folder as folder_mod
from .client import API, HEADER, SERVICE
from .state import compare_plans, compare_steps, decision_local, plan_approval, satisfied, step_approval, summarize
from . import protocol as protocol_mod
from . import worktrees as worktrees_mod
from .errors import WaterriseError
from .types import (ID_RE, LOCAL_RE, PROTOCOLS_FOLDER, REPORTS_FOLDER, STATE_FOLDERS, jsonable, plan_hash,
                    plan_snapshot, step_hash, step_snapshot)
from .proc import hidden

STATIC = Path(__file__).resolve().parent / "static"
ASSETS = ("app.css", "app.js", "events.js", "marked.min.js", "marked.LICENSE.md",
          "geist.woff2", "geist-mono.woff2", "geist.LICENSE.txt")
MAX_BODY = 256 * 1024
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
# A report's document (report-scripts, amos, 2026-10-18: scripts allowed,
# sandboxed). `sandbox allow-scripts` without allow-same-origin gives it an
# opaque origin wherever it is opened, framed by the page or on its own;
# `default-src 'none'` leaves it no fetch, no remote script, style, font or
# image, and no way to send anything out; only the page may frame it.
REPORT_CSP = ("sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; "
              "style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; "
              "base-uri 'none'; form-action 'none'; frame-ancestors 'self'")


def _version() -> str:
    from . import __version__

    h = hashlib.sha256()
    for name in ASSETS:
        h.update((STATIC / name).read_bytes())
    return f"{__version__}-{h.hexdigest()[:8]}"


# --------------------------------------------------------------------------- #
# the registry
# --------------------------------------------------------------------------- #

class Registry:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.repos: list[dict] = []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.repos = [r for r in data.get("repos", []) if isinstance(r, dict) and r.get("path")]
        except (OSError, ValueError, AttributeError):
            self.repos = []

    def save(self) -> None:
        answers_mod.write(self.path, {"repos": self.repos})

    def by_slug(self, slug: str) -> dict | None:
        return next((r for r in self.repos if r.get("slug") == slug), None)

    def register(self, path: str, planning: str, name: str, home: str | None = None) -> dict:
        """Register a project, or update its planning folder. A project keeps
        its home: moving it between homes is `projects home`, not this. A
        linked worktree registers its main checkout: one entry per repository."""
        repo = Path(path).resolve()
        place = context.locate(repo)
        if place and place.linked:
            planning = str(place.in_main(Path(planning).resolve()))
            repo = place.in_main(repo)
        with self.lock:
            for r in self.repos:
                if Path(r["path"]) == repo:
                    have = r.get("home") or "repo"
                    if home and home != have:
                        raise HomeConflict(f"{r['slug']} plans in its {'repository' if have == 'repo' else 'central home'}"
                                           f" already")
                    if have == "repo":
                        r["planning"] = planning
                    r.setdefault("home", have)
                    self.save()
                    return r
            base = re.sub(r"[^a-z0-9-]+", "-", (name or repo.name).lower()).strip("-") or "repo"
            taken = {r.get("slug") for r in self.repos}
            slug = base
            if slug in taken:
                slug = f"{base}-{re.sub(r'[^a-z0-9-]+', '-', repo.name.lower()).strip('-')}"
            n = 2
            while slug in taken:
                slug = f"{base}-{n}"
                n += 1
            home = home or "repo"
            if home == "central":
                central.ensure()
                planning = str(central.planning_for(slug))
            entry = {"path": str(repo), "planning": planning, "name": name or repo.name, "slug": slug,
                     "home": home, "common": _git_common(repo), "added": answers_mod.now()}
            self.repos.append(entry)
            self.save()
            return entry

    def collapse_worktrees(self) -> list[str]:
        """One entry per repository, for a registry written when a linked
        worktree could be its own project. An entry of a linked worktree goes
        if its main checkout is registered; otherwise it becomes the main
        checkout's. Nothing on disk changes. What was done, for the log."""
        said: list[str] = []
        with self.lock:
            paths = {Path(r["path"]) for r in self.repos}
            kept: list[dict] = []
            for r in self.repos:
                old = Path(r["path"])
                place = context.locate(old) if old.is_dir() else None
                if not place or not place.linked:
                    kept.append(r)
                    continue
                main = place.in_main(old)
                if main in paths:
                    if (r.get("home") or "repo") == "central":
                        # Its plans are only here; dropping it would hide them.
                        said.append(f"kept {r.get('slug')}: {old} is a worktree of {main}, "
                                    f"but plans in its own central home")
                        kept.append(r)
                    else:
                        said.append(f"dropped {r.get('slug')}: {old} is a worktree of {main}")
                    continue
                r["path"] = str(main)
                if (r.get("home") or "repo") == "repo" and r.get("planning"):
                    r["planning"] = str(place.in_main(Path(r["planning"])))
                paths.add(main)
                said.append(f"{r.get('slug')} is now {main}: {old} is a worktree of it")
                kept.append(r)
            if said:
                self.repos = kept
                self.save()
        return said

    def set_home(self, slug: str, home: str, planning: str) -> dict | None:
        with self.lock:
            entry = self.by_slug(slug)
            if entry is None:
                return None
            entry["home"], entry["planning"] = home, planning
            self.save()
            return entry

    def set_worktrees(self, slug: str, value: str | None) -> dict | None:
        """This machine's worktree folder for a project, over its planning.toml;
        None removes it, so planning.toml counts again."""
        with self.lock:
            entry = self.by_slug(slug)
            if entry is None:
                return None
            if value is None:
                entry.pop("worktrees", None)
            else:
                entry["worktrees"] = value
            self.save()
            return entry

    def forget(self, slug: str) -> bool:
        with self.lock:
            before = len(self.repos)
            self.repos = [r for r in self.repos if r.get("slug") != slug]
            if len(self.repos) != before:
                self.save()
                return True
            return False


class HomeConflict(Exception):
    """A registration that would change a project's home."""


def _git_common(repo: Path) -> str:
    """The git common directory, so worktrees group under their main checkout."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--path-format=absolute",
                              "--git-common-dir"], capture_output=True, text=True, timeout=5, encoding="utf-8",
                              stdin=subprocess.DEVNULL, **hidden())
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return str(repo)


def _branch(repo: Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(repo), "branch", "--show-current"],
                             capture_output=True, text=True, timeout=5, encoding="utf-8",
                             stdin=subprocess.DEVNULL, **hidden())
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


# --------------------------------------------------------------------------- #
# views
# --------------------------------------------------------------------------- #

def _folder(entry: dict) -> folder_mod.Folder:
    return folder_mod.read(Path(entry["planning"]), central.git_root(entry))


# ---- what the pages read, kept while nothing behind it changes (p-pw7ft8 s3-3) ---- #
#
# Only what the planning folders decide is kept: the folder as read, and its check
# report. The key is the signature the change events use (every file under the
# watched planning folders, protocols included), the registry entry, the top
# .gitignore and, for the report, today's date. What git says (the branch, the
# worktrees and their state) is asked on every request, since it changes without
# a planning file changing. Writes read the folder fresh. In memory only: one
# entry per project, gone with the service.

_views_lock = threading.Lock()
_kept: dict[tuple[str, str], tuple[tuple, object]] = {}


def _view_key(entry: dict, signature: str | None = None) -> tuple:
    root = Path(entry["path"])
    return (json.dumps(entry, sort_keys=True, default=str), signature or _signature(entry),
            layout._stamp(root / ".gitignore"))


def _kept_or(entry: dict, what: str, key: tuple, make):
    slot = (entry["slug"], what)
    with _views_lock:
        hit = _kept.get(slot)
    if hit is not None and hit[0] == key:
        return hit[1]
    value = make()
    with _views_lock:
        _kept[slot] = (key, value)
    return value


def _folder_view(entry: dict, key: tuple | None = None) -> folder_mod.Folder:
    """The folder for a page to read, the same object while nothing changed.
    Never for writing."""
    return _kept_or(entry, "folder", key or _view_key(entry), lambda: _folder(entry))


def _check_view(entry: dict, f: folder_mod.Folder, key: tuple):
    return _kept_or(entry, "check", (*key, _dt.date.today().isoformat()),
                    lambda: check_mod.run(f, git=False))


def forget_views() -> None:
    with _views_lock:
        _kept.clear()


def _watched(entry: dict) -> list[Path]:
    """The planning folders behind a project's pages: its own, and each worktree's
    under `worktrees/`, where a plan can live."""
    planning = Path(entry["planning"])
    if not planning.is_dir():
        return [planning]
    return [planning, *(h.planning for h in folder_mod.worktree_homes(planning.resolve(), central.git_root(entry).resolve()))]


def _signature(entry: dict, *, protocols: bool = True) -> str:
    return "".join(_tree_signature(p, protocols=protocols) for p in _watched(entry))


_ticks: dict[tuple, tuple[float, str]] = {}
_ticks_lock = threading.Lock()


def _tick_signature(entry: dict, *, protocols: bool = True) -> str:
    """`_signature` for the change streams: computed once for every stream that
    asks within the same half tick, so many open tabs cost one walk. Only for
    the streams: a page's request reads `_signature` itself, so it always sees
    its own write (p-pw7ft8 s3-4)."""
    key = (entry.get("slug"), entry.get("planning"), protocols)
    now = time.monotonic()
    with _ticks_lock:
        hit = _ticks.get(key)
    if hit is not None and now - hit[0] < Handler.EVENT_TICK / 2:
        return hit[1]
    sig = _signature(entry, protocols=protocols)
    with _ticks_lock:
        _ticks[key] = (now, sig)
    return sig


def _home_view(home: folder_mod.Home | None, known: list) -> dict | None:
    """Where a plan lives, when that is a worktree: its name, branch and path."""
    if home is None:
        return None
    branch = next((w.branch for w in known if w.path == home.checkout), None)
    return {"name": home.name, "branch": branch, "path": str(home.checkout)}


def repo_view(entry: dict, *, deep: bool = True) -> dict:
    missing = not Path(entry["planning"]).is_dir()
    out = {"slug": entry["slug"], "name": entry.get("name"), "path": entry["path"],
           "common": entry.get("common"), "missing": missing, "home": entry.get("home") or "repo",
           "planning": entry["planning"]}
    root = Path(entry["path"])
    out["worktree_count"] = worktrees_mod.count(root)
    if missing:
        return out
    key = _view_key(entry)
    f = _folder_view(entry, key)
    known = worktrees_mod.read(root) if deep else None
    out["typed"] = f.typed
    out["user"] = f.user
    out["branch"] = _branch(Path(entry["path"]))
    items, waiting = [], []
    ids = f.by_id()
    needed_by: dict[str, list[str]] = {}
    for it in f.items:
        for ref in _needs(it):
            needed_by.setdefault(ref.split("/", 1)[0], []).append(it.id)
    for it in f.items:
        if not it.parsed or not it.id:
            items.append({"id": it.id, "file": it.rel, "folder": it.folder, "title": it.title,
                          "kind": it.kind, "broken": True})
            continue
        ans = f.answers(it.id)
        s = summarize(it, ans, plan_approval_required=f.plan_approval, ids=ids).as_json()
        s["rank"] = folder_mod.rank(ans, it.folder)
        if known is not None:
            s["worktree"] = worktrees_mod.where(root, f.assignment(it.id), known)
            s["home"] = _home_view(it.home, known)
        # What has to be done before this plan can start, and what waits on it.
        s["needs"] = []
        for ref in _needs(it):
            target = ids.get(ref.split("/", 1)[0])
            s["needs"].append({"ref": ref, "id": target.id if target else None,
                               "title": target.title if target else None,
                               "folder": target.folder if target else None,
                               "done": satisfied(ref, it, ids)})
        s["needed_by"] = list(dict.fromkeys(needed_by.get(it.id, [])))
        if it.kind == "report":
            s["about"] = str(it.data.get("about") or "").split("/")[0] or None
        # The lanes it may be dragged to, and why not the others: the same
        # rules `planning move` checks, so the page can refuse a drop up front.
        if it.folder in STATE_FOLDERS:
            s["moves"] = {lane: check_mod.folder_rules(it, lane, ans, plan_approval_required=f.plan_approval)
                          for lane in STATE_FOLDERS if lane != it.folder}
        items.append(s)
        if s["for_user"]:
            waiting.append({"id": it.id, "title": it.title, "for_user": s["for_user"]})
    out["items"] = items
    out["waiting"] = waiting
    out["legacy"] = f.legacy
    out["forms"] = f.forms
    if known is not None:
        wf = worktrees_mod.folder_of(root)
        states = worktrees_mod.states(root, [w for w in known if w.assignable])
        out["worktree_folder"] = {"rel": wf.rel, "path": str(wf.path), "source": wf.source, "problem": wf.problem}
        out["worktrees"] = [
            {"name": w.name, "path": str(w.path), "branch": w.branch, "main": w.main,
             **states[w.path],
             "plans": [i["id"] for i in items if w.name in ((i.get("worktree") or {}).get("name"),
                                                             (i.get("home") or {}).get("name"))]}
            for w in known if w.assignable]
    if deep:
        rep = _check_view(entry, f, key)
        out["errors"] = rep.errors
        out["warnings"] = rep.warnings
    return out


def _needs(it: folder_mod.Item) -> list[str]:
    """An item's references to other items in its `needs`: whole plans, or
    a step or phase in one (`p-4k7x2m/s2-1`)."""
    if not it.parsed or not it.id:
        return []
    return [r for r in it.data.get("needs", []) or []
            if isinstance(r, str) and ID_RE.match(r.split("/", 1)[0])]


# What a broken sidecar still says plainly: read with a pattern, not TOML, so a
# typo elsewhere in the file doesn't take the report off its plan's page.
_RAW_FIELD = {k: re.compile(rf'^\s*{k}\s*=\s*"([^"\n]*)"', re.M) for k in ("id", "about", "title")}


def _raw(text: str, key: str) -> str:
    m = _RAW_FIELD[key].search(text)
    return m.group(1) if m else ""


def plan_reports(f: folder_mod.Folder, plan: folder_mod.Item) -> list[dict]:
    """Every report that belongs to `plan`, for its page (which, amos
    2026-10-24): those whose `about` names it or one of its steps, and those
    the plan names in `related` or `context`. A sidecar that doesn't parse is
    listed as broken, never left out; a report the plan names that doesn't
    exist is listed as missing."""
    named = {}
    for key in ("related", "context"):
        refs = plan.data.get(key) or []
        for ref in refs if isinstance(refs, list) else []:
            if isinstance(ref, str) and ref.startswith("h-"):
                named.setdefault(ref.split("/", 1)[0], key)
    out, seen = [], set()
    for r in f.items:
        if r.folder != REPORTS_FOLDER or not r.rel.endswith(".toml"):
            continue
        rid = r.id if r.parsed else _raw(r.text, "id")
        about = str(r.data.get("about") or "") if r.parsed else _raw(r.text, "about")
        links = []
        if about.split("/", 1)[0] == plan.id:
            links.append("step" if "/" in about else "about")
        if rid in named:
            links.append("named")
        if not links:
            continue
        seen.add(rid)
        entry = {"id": rid or None, "links": links, "step": about.split("/", 1)[1] if "/" in about else None,
                 "file": r.rel, "broken": not r.parsed}
        if r.parsed:
            entry |= {"title": r.title, "summary": r.data.get("summary", ""),
                      "updated": jsonable(r.data.get("updated")), "state": r.data.get("state", "finished")}
        else:
            entry |= {"title": _raw(r.text, "title") or r.name,
                      "problem": next((p.message for p in r.problems), "cannot be read")}
        out.append(entry)
    for rid, key in named.items():
        if rid not in seen:
            out.append({"id": rid, "links": ["named"], "missing": True, "file": None, "broken": False,
                        "title": None, "step": None, "named_in": key})
    return out


def _approval_view(a, compare) -> dict:
    """An approval as the page reads it: what changed instead of both snapshots,
    which would send a whole plan twice on every load."""
    view = {k: getattr(a, k) for k in ("status", "verdict", "at", "note", "sha256", "current")}
    if a.status == "stale":
        view["changes"] = compare(a.snapshot, a.now)
    return view


TIMELINE = 200      # entries a plan's page shows; the rest are counted


def timeline(it: folder_mod.Item, notes: list[dict]) -> tuple[list[dict], int]:
    """A plan's `[[log]]` and its steps' protocol notes, newest first
    (p-pw7ft8, decision `logs`). A log entry has a day but no time; it goes at
    the time of its step's last note that day, which is when it was written,
    or, without one, just after the log entry before it. The newest TIMELINE
    entries, and how many more."""
    last: dict[tuple[str, str], str] = {}
    for n in notes:
        key = (n["step"], n["at"][:10])
        last[key] = max(last.get(key, ""), n["at"])
    rows = [{"kind": "note", **n} for n in notes]
    before = ""                 # the log is written in order: no entry is older than the one above it
    for i, entry in enumerate(it.data.get("log") or []):
        if not isinstance(entry, dict):
            continue
        day = str(entry.get("on") or "")
        step = str(entry.get("step") or "")
        at = last.get((step, day)) or (before if before[:10] == day else f"{day} 00:00:00")
        at = max(at, before) if before[:10] == day else at
        before = at
        rows.append({"kind": "log", "at": at, "day": day, "step": step or None,
                     "text": str(entry.get("text") or ""), "commit": entry.get("commit"), "n": i})
    # Newest first; at the same moment the log, written after the note, first.
    rows.sort(key=lambda r: (r["at"], r["kind"] == "log", r.get("n", 0)), reverse=True)
    return rows[:TIMELINE], max(0, len(rows) - TIMELINE)


def item_view(entry: dict, item_id: str) -> dict | None:
    f = _folder_view(entry)
    it = f.get(item_id)
    if it is None:
        return None
    ans = f.answers(item_id)
    approvals = {}
    for s in it.steps():
        if s.get("gate"):
            a = step_approval(s, ans)
            approvals[s.get("id")] = _approval_view(a, compare_steps)
    view = {
        "repo": {"slug": entry["slug"], "name": entry.get("name")},
        "id": it.id, "kind": it.kind, "folder": it.folder, "file": it.rel,
        "data": jsonable(it.data), "answers": ans, "user": f.user,
        "plan_approval_required": f.plan_approval,
        "approvals": jsonable(approvals),
        "summary": summarize(it, ans, plan_approval_required=f.plan_approval, ids=f.by_id()).as_json(),
        "problems": [x.as_json() for x in check_mod.run(f, git=False).findings
                     if x.file in (it.rel, f"answers/{it.id}.json") and x.level != "info"],
    }
    if it.kind == "plan":
        root = Path(entry["path"])
        known = worktrees_mod.read(root)
        view["worktree"] = worktrees_mod.where(root, f.assignment(item_id), known)
        view["home"] = _home_view(it.home, known)
        view["worktrees"] = [w.name for w in known if w.assignable]
        view["reports"] = plan_reports(f, it)
        view["protocols"] = protocol_mod.summaries(f.root, it.id)
        view["timeline"], view["timeline_older"] = timeline(it, protocol_mod.notes(f.root, it.id))
    if it.kind == "report":
        doc = check_mod.report_document(it)
        version = int(doc.stat().st_mtime) if doc.is_file() else 0
        view["document"] = f"/r/{entry['slug']}/{it.id}/report.html?v={version}"
        about = str(it.data.get("about") or "").split("/")[0]
        target = f.get(about) if about else None
        view["about"] = {"id": target.id, "title": target.title} if target else None
    if it.kind == "plan":
        pa = plan_approval(it, ans)
        view["plan_approval"] = jsonable(_approval_view(pa, compare_plans))
    return view


def _tree_signature(root: Path, *, protocols: bool = True) -> str:
    """Names, sizes and mtimes of everything under `root`: changes when any
    file is written, added, moved or deleted. Polled, not inotify: stdlib-only.
    One `scandir` walk, which stats as it lists (p-pw7ft8 s3-4).

    Without `protocols`, the protocols folder counts by the mtimes of its plan
    folders only: a protocol that is added shows, one that grows does not. The
    index needs no more; a plan's own page watches every line."""
    h = hashlib.sha256()
    stack = [str(root)]
    top = os.path.join(str(root), protocol_mod.FOLDER)
    seen = False
    while stack:
        d = stack.pop()
        try:
            entries = sorted(os.scandir(d), key=lambda x: x.name)
        except OSError:
            continue
        seen = True
        shallow = not protocols and d == top
        for x in entries:
            if x.name.endswith(".lock") or x.name.endswith(".tmp"):
                continue
            try:
                # A real stat, not x.stat(): on Windows that is the listing's
                # cached copy, which lags a write and changes a folder's entry
                # when a file in it grows (p-pw7ft8 s5-2). On POSIX the two are
                # the same lstat.
                st = os.stat(x.path, follow_symlinks=False)
                is_dir = x.is_dir(follow_symlinks=False)
            except OSError:
                continue
            h.update(f"{x.path}:{st.st_mtime_ns}:{st.st_size};".encode())
            if is_dir and not shallow:
                stack.append(x.path)
    if not seen:
        h.update(b"missing")
    return h.hexdigest()


def _etag(*paths: Path) -> str:
    h = hashlib.sha256()
    for p in paths:
        try:
            st = p.stat()
            h.update(f"{p}:{st.st_mtime_ns}:{st.st_size};".encode())
        except OSError:
            h.update(f"{p}:-;".encode())
    return '"' + h.hexdigest()[:20] + '"'


# --------------------------------------------------------------------------- #
# the handler
# --------------------------------------------------------------------------- #

class Handler(BaseHTTPRequestHandler):
    server_version = "waterrise"
    registry: Registry
    port: int
    token: str
    version: str
    instance: str
    shell: bytes

    # ---- plumbing ---------------------------------------------------------- #

    def log_message(self, fmt: str, *args) -> None:
        # Writes and errors only; a line per poll would bury them.
        if self.command in ("PUT", "POST", "DELETE") or (len(args) > 1 and str(args[1])[:1] in "45"):
            sys.stderr.write(f"{_dt.datetime.now():%Y-%m-%d %H:%M:%S} {self.command} {self.path} "
                             f"{args[1] if len(args) > 1 else ''}\n")

    def _send(self, status: int, body: bytes = b"", ctype: str = "application/json",
              extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, obj: object, extra: dict | None = None) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False, default=str).encode(), extra=extra)

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}")

    def _write_ok(self) -> bool:
        if self.headers.get(HEADER) != "1":
            self._error(403, f"missing {HEADER}")
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"):
            self._error(403, "wrong Origin")
            return False
        return True

    def _authorized(self) -> bool:
        given = self.headers.get("Authorization", "")
        return secrets.compare_digest(given, f"Bearer {self.token}")

    def _read_body(self) -> bytes | None:
        """The request's body, read before anything is answered: on Windows a
        socket closed with unread data in it is reset, and the client gets a
        ConnectionAbortedError instead of the answer."""
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            self._error(400, "bad Content-Length")
            return None
        if length > MAX_BODY:
            self.close_connection = True
            self._error(413, "body too large")
            return None
        return self.rfile.read(length) if length > 0 else b""

    def _body(self) -> dict | None:
        try:
            data = json.loads(self._raw or b"{}")
        except ValueError:
            self._error(400, "not JSON")
            return None
        if not isinstance(data, dict):
            self._error(400, "not a JSON object")
            return None
        return data

    def _route(self) -> list[str]:
        return [unquote(p) for p in urlsplit(self.path).path.split("/") if p]

    # ---- GET ---------------------------------------------------------------- #

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._error(403, "wrong Host")
        parts = self._route()
        if not parts or parts[0] == "r" and (len(parts) == 2 or len(parts) >= 3 and parts[2] in ("i", "m")):
            return self._send(200, self.shell, "text/html; charset=utf-8",
                              {"Content-Security-Policy": CSP})
        if parts[0] == "static" and len(parts) == 3 and parts[1] == self.version and parts[2] in ASSETS:
            data = (STATIC / parts[2]).read_bytes()
            ext = parts[2].rsplit(".", 1)[-1]
            ctype = ("font/woff2" if ext == "woff2" else
                     {"js": "text/javascript", "css": "text/css"}.get(ext, "text/plain") + "; charset=utf-8")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
            return None
        if parts[0] == "r" and len(parts) >= 4 and parts[2] == "f":
            return self._file(parts[1], parts[3:], raw=True)
        if parts[0] == "r" and len(parts) == 4 and parts[3] == "report.html" and ID_RE.match(parts[2]):
            return self._report(parts[1], parts[2])
        if parts[0] != "api":
            return self._error(404, "not found")
        api = parts[1:]
        if api == ["health"]:
            return self._json(200, {"service": SERVICE, "api": API, "version": self.version,
                                    "instance": self.instance, "pid": os.getpid(), "port": self.port})
        if api == ["repos"]:
            return self._json(200, {"repos": [repo_view(r, deep=False) for r in self.registry.repos]})
        if api == ["events"]:
            return self._events()
        if len(api) >= 2 and api[0] == "r":
            entry = self.registry.by_slug(api[1])
            if entry is None:
                return self._error(404, "no such repository")
            if len(api) == 2:
                return self._json(200, repo_view(entry))
            if len(api) == 4 and api[2] == "item":
                if not ID_RE.match(api[3]):
                    return self._error(404, "no such item")
                f = Path(entry["planning"])
                fold = folder_mod.read(f, central.git_root(entry))
                it = fold.get(api[3])
                if it is None:
                    return self._error(404, "no such item")
                paths = [it.path, fold.answers_path(it.id), fold.assignments_path, f, worktrees_mod.folder_of(Path(entry["path"])).path,
                         *(h.planning / it.folder for h in folder_mod.worktree_homes(fold.root, fold.repo))]
                if it.kind == "plan":
                    # Its page lists its reports and its steps' protocols: a
                    # new, changed or removed one must not be answered with a 304.
                    reports = f / REPORTS_FOLDER
                    protocols = f / PROTOCOLS_FOLDER / it.id
                    paths += [reports, *sorted(reports.glob("*.toml")), protocols, *sorted(protocols.glob("*.md"))]
                tag = _etag(*paths)
                if self.headers.get("If-None-Match") == tag:
                    return self._send(304, extra={"ETag": tag})
                view = item_view(entry, api[3])
                return self._json(200, view, {"ETag": tag})
            if len(api) == 5 and api[2] == "protocol":
                # A step's protocol, as text: read-only, and only a name the
                # pattern allows, so nothing outside protocols/ can be asked for.
                if not ID_RE.match(api[3]) or not LOCAL_RE.match(api[4]):
                    return self._error(404, "no such protocol")
                path = protocol_mod.path_for(Path(entry["planning"]), api[3], api[4])
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    return self._error(404, "no such protocol")
                return self._json(200, {"text": text, "summary": protocol_mod.summary(path)},
                                  {"ETag": _etag(path)})
            if len(api) >= 4 and api[2] == "file":
                return self._file(api[1], api[3:], raw=False)
        return self._error(404, "not found")

    # How often an open page's files are looked at, and how often a quiet
    # stream says it is still there (proxies and browsers drop idle ones).
    EVENT_TICK = 0.5
    EVENT_PING = 15.0

    def _events(self) -> None:
        """A Server-Sent Events stream: `change` whenever a file behind the page
        changes. The page reloads its data on it; `hello` carries the version, so
        a page served by an older service reloads itself after an upgrade."""
        from urllib.parse import parse_qs

        q = parse_qs(urlsplit(self.path).query)
        scope, slug = (q.get("scope") or ["index"])[0], (q.get("slug") or [""])[0]

        def signatures() -> dict[str, str]:
            """A signature per repository behind the stream, and "" for the registry."""
            if scope == "repo":
                entry = self.registry.by_slug(slug)
                return {slug: _tick_signature(entry)} if entry else {}
            # scope=all is what the shared worker holds for every tab, plan pages
            # with their live protocols included; only an index page's own
            # stream may skip what protocols say.
            sigs = {r["slug"]: _tick_signature(r, protocols=scope != "index") for r in self.registry.repos}
            sigs[""] = _etag(self.registry.path)
            return sigs

        def change(last: dict[str, str], now: dict[str, str]) -> bytes:
            if scope != "all":
                return b"{}"
            slugs = sorted(k for k in last.keys() | now.keys() if k and last.get(k) != now.get(k))
            return json.dumps({"index": True, "slugs": slugs}).encode()

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        # The stream stays open, so the connection must not be reused afterwards.
        self.close_connection = True
        try:
            self.wfile.write(f"event: hello\ndata: {json.dumps({'version': self.version})}\n\n".encode())
            self.wfile.flush()
            last, quiet = signatures(), 0.0
            while not getattr(self.server, "stopping", False):
                time.sleep(self.EVENT_TICK)
                now = signatures()
                if now != last:
                    data = change(last, now)
                    last, quiet = now, 0.0
                    self.wfile.write(b"event: change\ndata: " + data + b"\n\n")
                    self.wfile.flush()
                else:
                    quiet += self.EVENT_TICK
                    if quiet >= self.EVENT_PING:
                        quiet = 0.0
                        self.wfile.write(b": still here\n\n")
                        self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _report(self, slug: str, item_id: str) -> None:
        entry = self.registry.by_slug(slug)
        if entry is None:
            return self._error(404, "no such repository")
        f = _folder(entry)
        it = f.get(item_id)
        if it is None or it.kind != "report":
            return self._error(404, "no such report")
        doc = check_mod.report_document(it).resolve()
        if not doc.is_relative_to(it.path.parent.resolve()) or not doc.is_file():
            return self._error(404, "the report's document is missing")
        return self._send(200, doc.read_bytes(), "text/html; charset=utf-8",
                          {"Content-Security-Policy": REPORT_CSP, "Referrer-Policy": "no-referrer",
                           "Cache-Control": "no-store"})

    def _file(self, slug: str, rel: list[str], *, raw: bool) -> None:
        entry = self.registry.by_slug(slug)
        if entry is None:
            return self._error(404, "no such repository")
        root = Path(entry["planning"]).resolve()
        target = (root / "/".join(rel)).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return self._error(404, "no such file")
        data = target.read_bytes()
        if raw:
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or target.suffix in (".md", ".toml", ".json"):
                ctype = ("text/html" if target.suffix == ".html" else "text/plain") + "; charset=utf-8"
            return self._send(200, data, ctype, {"Content-Security-Policy": "sandbox"})
        return self._json(200, {"path": "/".join(rel), "text": data.decode("utf-8", errors="replace")})

    # ---- writes ------------------------------------------------------------- #

    def do_POST(self) -> None:  # noqa: N802
        self._write("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._write("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._write("DELETE")

    def _write(self, method: str) -> None:
        self._raw = self._read_body()
        if self._raw is None:
            return None
        if not self._host_ok():
            return self._error(403, "wrong Host")
        if not self._write_ok():
            return None
        parts = self._route()
        if len(parts) < 2 or parts[0] != "api":
            return self._error(404, "not found")
        api = parts[1:]

        if api == ["shutdown"] and method == "POST":
            if not self._authorized():
                return self._error(403, "the control API needs the token")
            self._json(200, {"stopping": True})
            self.server.stopping = True
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return None
        if api == ["repos"] and method == "POST":
            if not self._authorized():
                return self._error(403, "the control API needs the token")
            body = self._body()
            if body is None:
                return None
            path, planning, home = body.get("path"), body.get("planning"), body.get("home")
            if not isinstance(path, str) or not isinstance(planning, str) or not Path(path).is_absolute():
                return self._error(400, "path and planning must be absolute paths")
            if home is not None and home not in central.HOMES:
                return self._error(400, f"home must be one of {', '.join(central.HOMES)}")
            known = next((r for r in self.registry.repos if Path(r["path"]) == Path(path).resolve()), None)
            if (home or (known or {}).get("home") or "repo") == "repo" and \
                    not Path(planning).resolve().is_relative_to(Path(path).resolve()):
                return self._error(400, "the planning folder must be inside the repository")
            if not Path(path).is_dir():
                return self._error(400, "no such directory")
            try:
                return self._json(200, self.registry.register(path, str(Path(planning).resolve()),
                                                              str(body.get("name") or ""), home))
            except HomeConflict as e:
                return self._error(409, str(e))
        if len(api) == 3 and api[0] == "repos" and api[2] == "home" and method == "PUT":
            if not self._authorized():
                return self._error(403, "a project's home is changed by `waterrise projects home`")
            body = self._body()
            if body is None:
                return None
            home, planning = body.get("home"), body.get("planning")
            if home not in central.HOMES or not isinstance(planning, str) or not Path(planning).is_absolute():
                return self._error(400, f"home must be one of {', '.join(central.HOMES)}, planning an absolute path")
            entry = self.registry.set_home(api[1], home, str(Path(planning).resolve()))
            if entry is None:
                return self._error(404, "no such repository")
            return self._json(200, entry)
        if len(api) == 3 and api[0] == "repos" and api[2] == "worktrees" and method == "PUT":
            if not self._authorized():
                return self._error(403, "a project's worktree folder is set by `waterrise projects worktrees`")
            body = self._body()
            if body is None:
                return None
            value = body.get("worktrees")
            if value is not None and (why := layout.invalid(value)):
                return self._error(400, f"worktrees {why}")
            entry = self.registry.set_worktrees(api[1], value)
            if entry is None:
                return self._error(404, "no such repository")
            return self._json(200, entry)
        if len(api) == 2 and api[0] == "repos" and method == "DELETE":
            entry = self.registry.by_slug(api[1])
            if entry is None:
                return self._error(404, "no such repository")
            if not self._authorized() and Path(entry["path"]).exists():
                return self._error(403, "only a repository that no longer exists can be forgotten from the page")
            self.registry.forget(api[1])
            return self._json(200, {"forgotten": api[1]})

        if len(api) >= 4 and api[0] == "r":
            entry = self.registry.by_slug(api[1])
            if entry is None:
                return self._error(404, "no such repository")
            body = self._body()
            if body is None:
                return None
            try:
                if api[2] == "order" and method == "PUT" and len(api) == 4:
                    return self._order_write(entry, api[3], body)
                if api[2] == "move" and method == "PUT" and len(api) == 4:
                    return self._move(entry, api[3], body)
                return self._item_write(entry, method, api[2], api[3], api[4] if len(api) > 4 else None, body)
            finally:
                # The central repository is the record of a central project's
                # answers; a click is committed like any command.
                if central.is_central(entry):
                    central.commit(entry["slug"], f"{entry['slug']}: {api[2]} {'/'.join(api[3:])} (the page)")
        return self._error(404, "not found")

    def _order_write(self, entry: dict, folder: str, body: dict) -> None:
        """The user's order of one folder's items. The page sends the whole
        lane, so an item added or moved away since it was drawn is a conflict
        rather than a guess; each item whose place changed gets its rank in its
        own answers file."""
        if folder not in STATE_FOLDERS:
            return self._error(400, f"only {', '.join(STATE_FOLDERS)} have an order")
        ids = body.get("ids")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
            return self._error(400, "ids must be a list of item ids, each once")
        f = _folder(entry)
        here = [it.id for it in f.items if it.folder == folder and it.parsed and it.id]
        if sorted(here) != sorted(ids):
            return self._error(409, f"{folder}/ has changed since the page was drawn; reload it")
        changed = 0
        for n, iid in enumerate(ids, 1):
            ans = f.answers(iid)
            if folder_mod.rank(ans, folder) == n:
                continue

            def change(d: dict, n=n) -> None:
                d["order"] = {"folder": folder, "rank": n, "at": answers_mod.now()}
            answers_mod.update(f.answers_path(iid), iid, change)
            changed += 1
        return self._json(200, {"saved": True, "changed": changed})

    def _move(self, entry: dict, item_id: str, body: dict) -> None:
        """Move an item to another lane, if that folder's rules allow it: what
        `planning move` does. `ids`, if given, is the new lane's order with the
        item in it, saved as the drag left it; otherwise the item goes last."""
        to, ids = body.get("folder"), body.get("ids")
        if to not in STATE_FOLDERS:
            return self._error(400, f"folder must be one of {', '.join(STATE_FOLDERS)}")
        if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)
                                or len(set(ids)) != len(ids)):
            return self._error(400, "ids must be a list of item ids, each once")
        f = _folder(entry)
        it = f.get(item_id) if ID_RE.match(item_id) else None
        if it is None:
            return self._error(404, "no such item")
        if it.folder == to:
            return self._json(200, {"moved": False, "folder": to})
        if it.folder not in STATE_FOLDERS:
            return self._error(400, f"an item in {it.folder}/ does not move between lanes")
        why = check_mod.folder_rules(it, to, f.answers(it.id), plan_approval_required=f.plan_approval)
        if why:
            return self._error(409, f"{it.id} cannot move to {to}/: " + "; ".join(why))
        if ids is not None:
            here = [x.id for x in f.items if x.folder == to and x.parsed and x.id]
            if sorted(here + [it.id]) != sorted(ids):
                return self._error(409, f"{to}/ has changed since the page was drawn; reload it")
        try:
            target = f.move(it, to)
        except FileExistsError:
            return self._error(409, f"{to}/ already has a file named {it.rel.split('/', 1)[1]}")
        except (OSError, subprocess.SubprocessError) as e:
            return self._error(500, f"the move failed: {e}")
        if ids is not None:
            for n, iid in enumerate(ids, 1):
                if iid != it.id and folder_mod.rank(f.answers(iid), to) == n:
                    continue

                def change(d: dict, n=n) -> None:
                    d["order"] = {"folder": to, "rank": n, "at": answers_mod.now()}
                answers_mod.update(f.answers_path(iid), iid, change)
        return self._json(200, {"moved": True, "folder": to, "file": target.relative_to(it.home.planning if it.home else f.root).as_posix()})

    def _item_write(self, entry: dict, method: str, what: str, item_id: str, local: str | None,
                    body: dict) -> None:
        f = _folder(entry)
        it = f.get(item_id) if ID_RE.match(item_id) else None
        if it is None:
            return self._error(404, "no such item")
        path = f.answers_path(it.id)

        if what == "worktree" and method == "PUT" and local is None:
            name = body.get("name")
            if name is not None and not isinstance(name, str):
                return self._error(400, "name must be a worktree's name, or null")
            try:
                warning = worktrees_mod.assign(Path(entry["path"]), f, it, name, by="page")
            except WaterriseError as e:
                return self._error(409, e.message)
            return self._json(200, {"worktree": worktrees_mod.where(Path(entry["path"]), f.assignment(it.id)),
                                    "warning": warning})

        if what == "decision" and method == "PUT" and local:
            dec = next((d for d in it.decisions() if decision_local(it, d) == local), None)
            if dec is None:
                return self._error(404, "no such decision")
            if dec.get("resolved"):
                return self._error(409, "this decision is resolved; comment to reopen it")
            selected = body.get("selected") or []
            text = body.get("text") or ""
            oids = [o.get("id") for o in dec.get("option", [])]
            if not isinstance(selected, list) or any(s not in oids for s in selected):
                return self._error(400, "selected must name options of this decision")
            if len(selected) > 1 and not dec.get("multiple"):
                return self._error(400, "this decision takes one option")
            if not isinstance(text, str):
                return self._error(400, "text must be a string")
            if text and dec.get("own_answer") is False:
                return self._error(400, "this decision takes no own answer")

            def change(d: dict) -> None:
                d.setdefault("decisions", {})[local] = {"selected": selected, "text": text,
                                                        "at": answers_mod.now()}
            answers_mod.update(path, it.id, change)
            return self._json(200, {"saved": True})

        if what == "step" and method == "PUT" and local:
            step = next((s for s in it.steps() if s.get("id") == local), None)
            if step is None:
                return self._error(404, "no such step")
            if step.get("by") != "user":
                return self._error(403, "only the user's own steps are ticked in the page")
            state = body.get("state")
            if state not in ("done", "open"):
                return self._error(400, "state must be done or open")

            def change(d: dict) -> None:
                d.setdefault("steps", {})[local] = {"state": state, "at": answers_mod.now()}
            answers_mod.update(path, it.id, change)
            return self._json(200, {"saved": True})

        if what == "approval" and method == "PUT":
            verdict = body.get("verdict")
            note = body.get("note") or ""
            if verdict not in ("approved", "disapproved", None) or not isinstance(note, str):
                return self._error(400, "verdict must be approved, disapproved or null")
            shown = body.get("sha256")      # the text the user was looking at
            if shown is not None and not isinstance(shown, str):
                return self._error(400, "sha256 must be a string")
            if local:
                step = next((s for s in it.steps() if s.get("id") == local), None)
                if step is None:
                    return self._error(404, "no such step")
                if not step.get("gate"):
                    return self._error(400, "this step has no gate")
                if verdict is not None and shown is not None and shown != step_hash(step):
                    return self._error(409, "this step has changed since it was drawn; look at it again")
                record = {"verdict": verdict, "note": note, "at": answers_mod.now(),
                          "sha256": step_hash(step), "snapshot": jsonable(step_snapshot(step))}

                def change(d: dict) -> None:
                    if verdict is None:
                        d.setdefault("approvals", {}).pop(local, None)
                    else:
                        d.setdefault("approvals", {})[local] = record
            else:
                if it.kind != "plan":
                    return self._error(400, "only a plan is approved as a whole")
                if verdict is not None and shown is not None and shown != plan_hash(it.data):
                    return self._error(409, "this plan has changed since it was drawn; look at it again")
                record = {"verdict": verdict, "note": note, "at": answers_mod.now(),
                          "sha256": plan_hash(it.data), "snapshot": jsonable(plan_snapshot(it.data))}

                def change(d: dict) -> None:
                    if verdict is None:
                        d.pop("plan_approval", None)
                    else:
                        d["plan_approval"] = record
            answers_mod.update(path, it.id, change)
            return self._json(200, {"saved": True})

        if what == "pause" and method == "PUT" and local is None:
            if it.kind != "plan":
                return self._error(400, "only a plan is paused")
            want = body.get("paused")
            if not isinstance(want, bool):
                return self._error(400, "paused must be true or false")

            def change(d: dict) -> None:
                if want:
                    d["paused"] = {"at": answers_mod.now(), "by": "page"}
                else:
                    d.pop("paused", None)
            answers_mod.update(path, it.id, change)
            return self._json(200, {"paused": want})

        if what == "comment" and method == "POST":
            on = body.get("on") or ""
            text = (body.get("text") or "").strip()
            if not isinstance(on, str) or (on and on not in it.locals()):
                return self._error(400, "on must be empty or a local id of this item")
            if not text:
                return self._error(400, "a comment needs text")
            created = {}

            def change(d: dict) -> None:
                cid = answers_mod.next_comment_id(d)
                created["id"] = cid
                d.setdefault("comments", []).append({"id": cid, "on": on, "text": text,
                                                     "at": answers_mod.now()})
            answers_mod.update(path, it.id, change)
            return self._json(200, {"id": created["id"]})

        return self._error(404, "not found")


# --------------------------------------------------------------------------- #
# running
# --------------------------------------------------------------------------- #

def _registry(state: Path) -> Registry:
    """The registry, filled on the first start from butler's, every entry
    planning in its repository. butler's file stays, for an older harness
    that still runs."""
    from .client import REGISTRY, legacy_registry

    reg = Registry(state / REGISTRY)
    if not reg.path.exists():
        old = Registry(legacy_registry())
        if old.repos:
            reg.repos = [r | {"home": r.get("home", "repo")} for r in old.repos]
            reg.save()
            sys.stderr.write(f"{answers_mod.now()} imported {len(reg.repos)} project(s) "
                             f"from {old.path}\n")
    for line in reg.collapse_worktrees():
        sys.stderr.write(f"{answers_mod.now()} registry: {line}\n")
    return reg


def make_server(port: int, state: Path) -> ThreadingHTTPServer:
    state.mkdir(parents=True, exist_ok=True)
    from .client import token as _token

    os.environ[dirs.STATE_ENV] = str(state)
    version = _version()
    shell = (STATIC / "app.html").read_text(encoding="utf-8").replace("{{V}}", version).encode()
    handler = type("H", (Handler,), {
        "registry": _registry(state), "port": port, "token": _token(),
        "version": version, "instance": secrets.token_hex(8), "shell": shell,
    })
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    handler.port = server.server_address[1]
    return server


def main(argv: list[str]) -> None:
    port, state = int(argv[0]), Path(argv[1])
    server = make_server(port, state)
    h = server.RequestHandlerClass
    info = {"service": SERVICE, "pid": os.getpid(), "port": h.port, "api": API, "version": h.version,
            "instance": h.instance, "started": answers_mod.now()}
    answers_mod.write(state / "service.json", info)
    sys.stderr.write(f"{info['started']} serving on http://127.0.0.1:{h.port}/ "
                     f"(waterrise {h.version}, pid {os.getpid()})\n")
    sys.stderr.flush()

    def stop(*_):
        server.stopping = True
        threading.Thread(target=server.shutdown, daemon=True).start()

    # SIGTERM from systemd; SIGBREAK is Ctrl+Break in a Windows console. A
    # detached Windows service gets neither and stops through /api/shutdown.
    for name in ("SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), stop)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        try:
            current = json.loads((state / "service.json").read_text(encoding="utf-8"))
            if current.get("instance") == h.instance:
                lock.remove(state / "service.json")
        except (OSError, ValueError):
            pass
        sys.stderr.write(f"{answers_mod.now()} stopped\n")
