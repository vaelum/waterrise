"""work-plan: an MCP server that lets an agent keep working a plan to the end.

The problem it solves: an agent working a plan in `planning/` stops at every
point that needs amos (a decision, an approval, a comment to answer, a step of
amos's own) and ends its turn, and amos has to come back to the chat to say
"go on". This server gives the agent a tool that *waits*: `wait_for_plan`
blocks until amos has done something in the planning page that the agent can
act on, and returns what that is. An agent started with the `work_plan` prompt
loops work → wait → work until the plan is done, so amos answers in the page
and never in the chat.

What it reads, and what it does not:

  * everything it knows comes from `waterrise status <id> --json`, run by the
    installed waterrise for the plan's project. It never reads or writes
    `planning/answers/` and never calls the planning service's API — the same
    rules the agent follows;
  * it changes nothing. Editing the plan, `gate`, `resolve` and `move` stay
    with the agent, where the transcript shows them.

The protocol is MCP over stdio (newline-delimited JSON-RPC), written out by
hand in `server.py` so it runs on a bare interpreter, like the rest of
waterrise. Everything about plans is in this module, as
pure functions of the status JSON where possible; that is what the tests feed.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .. import client
from ..install import command

# How often a wait asks `planning status`. One status is a fraction of a second
# of Python; amos clicking is minutes apart.
POLL_SECONDS = float(os.environ.get("WORK_PLAN_POLL_SECONDS", "15"))

# How long one wait blocks before it returns "nothing yet" and the agent calls
# again. Short enough that no client's tool timeout is near it, long enough
# that a quiet hour costs a handful of calls.
DEFAULT_WAIT_MINUTES = 10
MAX_WAIT_MINUTES = 60

DONE_STATES = ("done", "dropped")


class PlanError(Exception):
    """Something the agent should read and act on, not a crash."""


# --------------------------------------------------------------------------- #
# finding the plan
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Plan:
    repo: Path          # the project's root
    id: str             # p-xxxxxx

    @property
    def label(self) -> str:
        return f"{self.repo.name}:{self.id}"


def known_repos(cwd: Path | None = None) -> list[tuple[str, Path, Path]]:
    """(name, root, planning folder) of the projects to search for a bare id:
    the current one first, then every one `serve` has registered, in its order.

    Names repeat: a deploy tree that is a clone of a repository registers under
    the same name as the working checkout.
    """
    found: list[tuple[str, Path, Path]] = []
    registered = [(r.get("name") or Path(r["path"]).name, Path(r["path"]),
                   Path(r.get("planning") or Path(r["path"]) / "planning"))
                  for r in client.registry() if Path(r["path"]).is_dir()]
    if cwd is not None:
        top = _git_toplevel(cwd)
        if top is not None:
            found.append(next((e for e in registered if e[1] == top), (top.name, top, top / "planning")))
    for entry in registered:
        if entry[1] not in [e[1] for e in found]:
            found.append(entry)
    return found


def _git_toplevel(path: Path) -> Path | None:
    try:
        out = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return Path(out.stdout.strip()) if out.returncode == 0 else None


def _item_id(path: Path) -> str | None:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8")).get("id")
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return None


def _find_in(planning: Path, item_id: str) -> bool:
    if not planning.is_dir():
        return False
    for folder in ("draft", "todo", "in-progress", "done", "notes"):
        for f in (planning / folder).glob("*.toml"):
            if item_id in f.read_text(encoding="utf-8", errors="replace") and _item_id(f) == item_id:
                return True
    return False


def resolve(spec: str, cwd: Path | None = None) -> Plan:
    """A plan from what amos typed: `p-xxxxxx`, `repo:p-xxxxxx`, or the path
    to the plan's TOML file."""
    spec = spec.strip()
    if not spec:
        raise PlanError("no plan given: pass its id (p-xxxxxx), repo:id, or the path to its file")

    as_path = Path(spec).expanduser()
    if not as_path.is_absolute() and cwd is not None:
        as_path = cwd / as_path
    if spec.endswith(".toml") and as_path.is_file():
        item_id = _item_id(as_path)
        repo = _git_toplevel(as_path.parent)
        if not item_id or repo is None:
            raise PlanError(f"{as_path} is not a planning item inside a git repository")
        return Plan(repo=repo, id=item_id)

    repo_name, _, item_id = spec.rpartition(":")
    repos = known_repos(cwd)
    if repo_name and Path(repo_name).expanduser().is_dir():
        root = Path(repo_name).expanduser().resolve()
        repos = [next((e for e in repos if e[1] == root), (root.name, root, root / "planning"))]
    elif repo_name:
        named = [r for r in repos if r[0] == repo_name]
        if not named:
            raise PlanError(f"no registered repository named {repo_name!r}; "
                            f"known: {', '.join(sorted({r[0] for r in repos})) or 'none'} "
                            "(run `waterrise serve` in it once)")
        repos = named
    hits = [r for r in repos if _find_in(r[2], item_id)]
    if not hits:
        raise PlanError(f"no item {item_id} in "
                        f"{', '.join(sorted({r[0] for r in repos})) or 'any known repository'}")
    # The current repository wins, and otherwise the first-registered checkout
    # of a name (the working one, not a deploy tree cloned from it). Only
    # different repositories holding the same id is a real ambiguity.
    if len({h[0] for h in hits}) > 1:
        raise PlanError(f"{item_id} is in several repositories "
                        f"({', '.join(f'{h[0]} at {h[1]}' for h in hits)}); say which as repo:id")
    return Plan(repo=hits[0][1], id=item_id)


# --------------------------------------------------------------------------- #
# reading its state through waterrise
# --------------------------------------------------------------------------- #


def _waterrise(plan: Plan, *args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    """The installed waterrise, for the plan's project: the one version that
    reads every project, whatever its butler pins."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("WATERRISE_PROG", "WATERRISE_PLANNING_DIR", "WATERRISE_PROJECT_NAME")}
    env["WATERRISE_PROJECT"] = str(plan.repo)
    try:
        return subprocess.run([*command(), "--no-color", *args], cwd=plan.repo, env=env,
                              capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise PlanError(f"`waterrise {' '.join(args)}` took longer than {timeout:.0f}s") from None
    except OSError as e:
        raise PlanError(f"waterrise does not run: {e}") from None


def status(plan: Plan) -> dict:
    """`waterrise status <id> --json` for the one item."""
    out = _waterrise(plan, "status", plan.id, "--json")
    if out.returncode != 0:
        raise PlanError((out.stderr or out.stdout).strip() or f"planning status exited {out.returncode}")
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        raise PlanError(f"planning status did not print JSON: {out.stdout[:200]!r}") from None
    if isinstance(data, list):
        data = next((d for d in data if d.get("id") == plan.id), None)
    if not data:
        raise PlanError(f"planning status said nothing about {plan.id}")
    return data


_urls: dict[Path, str] = {}


def page_url(plan: Plan) -> str | None:
    """The item's page. `serve` starts the service if it is down,
    which is what amos needs to answer at all; its URL is cached per repo."""
    base = _urls.get(plan.repo)
    if base is None:
        out = _waterrise(plan, "serve")
        # The first URL is the repository's; later lines link items.
        for word in (out.stdout + "\n" + out.stderr).split():
            if word.startswith(("http://", "https://")):
                base = word
                break
        if base is None:
            return None
        _urls[plan.repo] = base
    return base.rstrip("/") + f"/i/{plan.id}"


# --------------------------------------------------------------------------- #
# what the state means for the agent
# --------------------------------------------------------------------------- #


@dataclass
class Assessment:
    verdict: str                           # "work", "waiting" or "done"
    agent: list[str] = field(default_factory=list)
    user: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = []
        if self.agent:
            out.append("For you, now:")
            out += [f"  - {a}" for a in self.agent]
        if self.user:
            out.append("Waiting on amos:")
            out += [f"  - {u}" for u in self.user]
        return out


def assess(st: dict) -> Assessment:
    """Whether the agent has something to do now, from one item's status.

    The agent has work when status lists anything `for_agent` (an answer to act
    on, a ticked user step, a disapproval, a comment, an open finding), when one
    of its steps is ready and either has no gate or an approved one, and when
    the item only needs moving between folders.
    """
    folder = st.get("folder", "")
    if folder == "done":
        return Assessment("done")

    agent = [f"{x['what']} {x.get('local') or ''}: {x.get('text', '')}".replace(" :", ":")
             for x in st.get("for_agent", [])]
    user = [f"{x['what']} {x.get('local') or ''}: {x.get('text', '')}".replace(" :", ":")
            for x in st.get("for_user", [])]

    whats = {x["what"] for x in st.get("for_user", [])}
    open_steps = st.get("open", [])
    # A draft's steps start once amos has approved it and it has moved to
    # todo; until then the work in it is answers, comments and the move.
    for s in [] if folder == "draft" else open_steps:
        if s.get("by") != "agent" or not s.get("ready") or s.get("state") not in ("open", "active"):
            continue
        if s.get("gate") and s.get("approval") != "approved":
            continue
        gated = " (gated and approved: run `planning gate` first)" if s.get("gate") else ""
        agent.append(f"step {s['id']}: {s.get('title', '')}{gated}")

    steps = st.get("steps", {})
    total, done = steps.get("total", 0), steps.get("done", 0)
    if st.get("kind") == "plan":
        if folder == "draft" and total and not whats & {"decision", "plan-approval"}:
            agent.append("the plan is approved (or needs no approval): `planning move` it to todo")
        if total and done == total and not st.get("for_agent"):
            agent.append("every step is done: finish the log and `planning move` it to done")

    return Assessment("work" if agent else "waiting", agent, user)


def fingerprint(st: dict) -> tuple:
    """What amos has done so far, as far as it shows in status. A wait ends
    when this changes even if no new work came of it (an approval of a step
    that still waits on another, say), so the agent can look."""
    return (
        tuple(sorted((x["what"], x.get("local") or "", x.get("text", "")) for x in st.get("for_user", []))),
        tuple(sorted((s.get("id"), s.get("approval"), s.get("ticked")) for s in st.get("open", []))),
        st.get("folder"),
    )


def describe(plan: Plan, st: dict, a: Assessment, url: str | None, head: str) -> str:
    steps = st.get("steps", {})
    lines = [head, "",
             f"{plan.label} — {st.get('title', '')}",
             f"repository: {plan.repo}   file: planning/{st.get('file', '')}",
             f"folder: {st.get('folder')}   steps: {steps.get('done', 0)}/{steps.get('total', 0)} done"
             + (f"   next: {st['next']}" if st.get("next") else "")]
    if url:
        lines.append(f"page: {url}")
    lines.append("")
    lines += a.lines() or ["Nothing open on either side."]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# the two tools
# --------------------------------------------------------------------------- #


def tool_status(spec: str, cwd: Path | None) -> str:
    plan = resolve(spec, cwd)
    st = status(plan)
    a = assess(st)
    head = {"work": "WORK: there is something for you to do now.",
            "waiting": "WAITING: everything left waits on amos; call wait_for_plan.",
            "done": "DONE: the plan is in done/."}[a.verdict]
    return describe(plan, st, a, page_url(plan), head)


def tool_wait(spec: str, cwd: Path | None, minutes: float | None,
              cancelled=lambda: False, progress=lambda done, total: None,
              poll: float | None = None, clock=time.monotonic, sleep=time.sleep) -> str:
    plan = resolve(spec, cwd)
    minutes = DEFAULT_WAIT_MINUTES if minutes is None else max(0.0, min(float(minutes), MAX_WAIT_MINUTES))
    poll = POLL_SECONDS if poll is None else poll
    st = status(plan)
    a = assess(st)
    if a.verdict != "waiting":
        head = ("NOT WAITING: there is already something for you to do; do it, then wait."
                if a.verdict == "work" else "DONE: the plan is in done/; there is nothing to wait for.")
        return describe(plan, st, a, page_url(plan), head)

    url = page_url(plan)
    before = fingerprint(st)
    start = clock()
    deadline = start + minutes * 60
    failures = 0
    while clock() < deadline:
        if cancelled():
            return "CANCELLED: the wait was cancelled by the client."
        sleep(min(poll, max(0.0, deadline - clock())))
        progress(clock() - start, minutes * 60)
        try:
            st = status(plan)
            failures = 0
        except PlanError as e:
            # A status that fails once is usually a file mid-save; three in a
            # row is something the agent has to look at.
            failures += 1
            if failures >= 3:
                return f"ERROR: planning status keeps failing for {plan.label}:\n{e}"
            continue
        a = assess(st)
        if a.verdict == "done":
            return describe(plan, st, a, url, "DONE: the plan moved to done/.")
        if a.verdict == "work":
            return describe(plan, st, a, url, "AMOS ACTED: there is something for you to do now.")
        if fingerprint(st) != before:
            return describe(plan, st, a, url,
                            "CHANGED: amos did something in the page, but nothing is ready for you yet. "
                            "Look, then wait again.")
    return describe(plan, st, a, url,
                    f"NOTHING YET after {minutes:g} min: still waiting on amos. Call wait_for_plan again.")
