"""What an item's files mean together: open, answered, approved, stale, waiting.

`check`, `status`, `list`, `gate` and the service all read state through here,
so "is this approval current?" has one answer everywhere.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from . import answers as answers_mod
from .folder import Item
from .types import ID_RE, jsonable, plan_hash, plan_snapshot, step_hash, step_snapshot

DONE_STEP = ("done", "dropped")


# ---- dependencies ------------------------------------------------------------- #

def satisfied(ref: str, item: Item, ids: dict | None = None) -> bool:
    """Is what `ref` names finished: a step done, a phase's steps all done or
    dropped, a decision resolved, a plan in done/? Without `ids` (the folder's
    items by id), a reference into another item counts as finished: it cannot
    be looked at, and should not hold anything up on a page."""
    if "/" in ref or ID_RE.match(ref):
        if ids is None:
            return True
        if "/" not in ref:
            target = ids.get(ref)
            return target is not None and target.folder == "done"
        head, local = ref.split("/", 1)
        target_item = ids.get(head)
    else:
        target_item, local = item, ref
    if target_item is None:
        return False
    kind = target_item.locals().get(local)
    node = target_item.find(local)
    if node is None:
        return False
    if kind == "step":
        return node.get("state") == "done"
    if kind == "phase":
        return all(s.get("state") in DONE_STEP for s in node.get("step", []))
    if kind == "decision":
        return bool(node.get("resolved"))
    return True


def waiting_on(step: dict, item: Item, ids: dict | None = None) -> list[str]:
    """What in the step's `needs` is not finished yet."""
    return [r for r in step.get("needs", []) or [] if isinstance(r, str) and not satisfied(r, item, ids)]


# ---- decisions ---------------------------------------------------------------- #

def decision_state(dec: dict, ans: dict, local: str) -> str:
    """resolved | answered | open."""
    if dec.get("resolved"):
        return "resolved"
    if answers_mod.answered(ans.get("decisions", {}).get(local)):
        return "answered"
    return "open"


def decision_local(item: Item, dec: dict) -> str:
    return item.id if item.kind == "decision" else str(dec.get("id", ""))


# ---- approvals ---------------------------------------------------------------- #

@dataclass
class Approval:
    status: str                 # none | approved | disapproved | stale
    verdict: str = ""           # what the user said, even when stale
    at: str = ""
    note: str = ""
    sha256: str = ""
    current: str = ""           # the hash of the step as it is now
    snapshot: object = None     # what was approved
    now: object = None          # what it is now

    @property
    def ok(self) -> bool:
        return self.status == "approved"

    def line(self, user: str) -> str:
        if self.status == "approved":
            return (f"approved in the page by {user} at {self.at}, "
                    f"unchanged since (sha256 {self.current[:12]}…)")
        if self.status == "disapproved":
            note = f": {self.note}" if self.note else ""
            return f"disapproved by {user} at {self.at}{note}"
        if self.status == "stale":
            return (f"{self.verdict} by {user} at {self.at}, but changed since "
                    f"(answered on sha256 {self.sha256[:12]}…, now {self.current[:12]}…): ask again")
        return f"not approved yet: {user} has not answered"


def _approval(entry: dict | None, current: str, now: object) -> Approval:
    if not entry or entry.get("verdict") not in ("approved", "disapproved"):
        return Approval("none", current=current, now=now)
    a = Approval(status=entry["verdict"], verdict=entry["verdict"], at=str(entry.get("at", "")),
                 note=str(entry.get("note") or ""), sha256=str(entry.get("sha256", "")),
                 current=current, snapshot=entry.get("snapshot"), now=now)
    if a.sha256 != current:
        a.status = "stale"
    return a


def step_approval(step: dict, ans: dict) -> Approval:
    return _approval(ans.get("approvals", {}).get(step.get("id")), step_hash(step), step_snapshot(step))


def plan_approval(item: Item, ans: dict) -> Approval:
    return _approval(ans.get("plan_approval"), plan_hash(item.data), plan_snapshot(item.data))


# ---- what changed since an answer ------------------------------------------------ #

PROSE_FIELDS = ("title", "goal", "details", "done_when")
# Below this share of surviving words, marking every other word reads worse than
# the old text followed by the new one.
REWRITTEN_BELOW = 0.45
_TOKEN = re.compile(r"\s+|\S+")


def word_runs(old: str, new: str) -> dict:
    """Two texts as runs of same, removed and added words, or, when little
    survives, as a rewritten passage."""
    a, b = _TOKEN.findall(old), _TOKEN.findall(new)
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    words = [x for x in a if not x.isspace()]
    kept = sum(1 for tag, i1, i2, _, _ in matcher.get_opcodes() if tag == "equal"
               for x in a[i1:i2] if not x.isspace())
    # Against the shorter text: words only added to, or only cut from, are marked.
    total = min(len(words), sum(1 for x in b if not x.isspace()))
    if total and kept / total < REWRITTEN_BELOW:
        return {"rewritten": True, "old": old, "new": new}
    runs: list[list[str]] = []

    def run(kind: str, text: str) -> None:
        if not text:
            return
        if runs and runs[-1][0] == kind:
            runs[-1][1] += text
        else:
            runs.append([kind, text])
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            run("same", "".join(a[i1:i2]))
        else:
            run("del", "".join(a[i1:i2]))
            run("add", "".join(b[j1:j2]))
    return {"rewritten": False, "old": old, "new": new, "runs": runs}


def _field_changes(old: dict, new: dict, skip: tuple[str, ...] = ("id", "step")) -> list[dict]:
    out = []
    for key in sorted(set(old) | set(new)):
        if key in skip or old.get(key) == new.get(key):
            continue
        before, after = old.get(key), new.get(key)
        change = {"field": key, "old": before, "new": after}
        if key in PROSE_FIELDS and isinstance(before, str) and isinstance(after, str):
            change["prose"] = word_runs(before, after)
        out.append(change)
    return out


def _moved(old: list[str], new: list[str]) -> set[str]:
    """Of the ids in both lists, those that changed places: what is outside the
    longest run of ids in the same order."""
    a = [x for x in old if x in new]
    b = [x for x in new if x in old]
    same: set[str] = set()
    for block in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        same.update(a[block.a:block.a + block.size])
    return set(a) - same


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _summary(fields: int, steps: int, moves: dict[str, int]) -> str:
    """"3 fields changed in 2 steps, 1 step added"."""
    parts = []
    if fields:
        parts.append(_plural(fields, "field") + " changed" + (f" in {_plural(steps, 'step')}" if steps else ""))
    for what in ("added", "removed", "moved"):
        for kind in ("step", "phase"):
            n = moves.get(f"{kind} {what}", 0)
            if n:
                parts.append(f"{_plural(n, kind)} {what}")
    return ", ".join(parts) or "Nothing differs"


UNAVAILABLE = {"available": False, "count": 0, "summary": "The earlier text was not kept, so the difference is unavailable"}


def compare_steps(old: object, new: dict) -> dict:
    """What changed in one step since its snapshot `old`."""
    if not isinstance(old, dict):
        return dict(UNAVAILABLE)
    fields = _field_changes(step_snapshot(jsonable(old)), jsonable(new))
    return {"available": True, "kind": "step", "fields": fields, "count": len(fields),
            "summary": _summary(len(fields), 0, {})}


def compare_plans(old: object, new: list) -> dict:
    """What changed in a plan's phases and steps since its snapshot `old`:
    matched by id, and each change once."""
    if not isinstance(old, list):
        return dict(UNAVAILABLE)
    # An older snapshot is read as a new one is made, so defaults filled in
    # since do not look like changes.
    old_phases = [dict(ph, step=[step_snapshot(s) for s in ph.get("step", [])])
                  for ph in jsonable(old) if isinstance(ph, dict)]
    new_phases = jsonable(new)
    old_by_id = {ph.get("id"): ph for ph in old_phases}
    new_by_id = {ph.get("id"): ph for ph in new_phases}
    old_steps = {s.get("id"): (s, ph.get("id")) for ph in old_phases for s in ph["step"]}
    new_steps = {s.get("id"): (s, ph.get("id")) for ph in new_phases for s in ph.get("step", [])}
    moved_phases = _moved(list(old_by_id), list(new_by_id))
    phases, same_phases = [], 0
    n_fields = n_steps = 0
    moves: dict[str, int] = {}

    def tally(what: str, n: int = 1) -> None:
        moves[what] = moves.get(what, 0) + n

    def line(s: dict) -> dict:
        return {"id": s.get("id"), "title": s.get("title")}

    for ph in new_phases:
        pid = ph.get("id")
        before = old_by_id.get(pid)
        fields = _field_changes(before, ph) if before else []
        here_old = [s.get("id") for s in before["step"]] if before else []
        here_new = [s.get("id") for s in ph.get("step", [])]
        moved_here = _moved(here_old, here_new)
        steps, same = [], []
        for s in ph.get("step", []):
            sid = s.get("id")
            if sid not in old_steps:
                steps.append(line(s) | {"status": "added", "step": s, "fields": []})
                if before is not None:      # in a new phase, the phase says it
                    tally("step added")
                continue
            was, was_in = old_steps[sid]
            changed = _field_changes(was, s)
            came_from = was_in if was_in != pid else None
            is_moved = came_from is not None or sid in moved_here
            if not changed and not is_moved:
                same.append(line(s))
                continue
            n_fields += len(changed)
            n_steps += bool(changed)
            tally("step moved", int(is_moved))
            steps.append(line(s) | {"status": "moved" if is_moved and not changed else "changed",
                                    "moved": is_moved, "from_phase": came_from, "fields": changed})
        for s in before["step"] if before else []:
            if s.get("id") not in new_steps:
                steps.append(line(s) | {"status": "removed", "step": s, "fields": []})
                tally("step removed")
        status = "added" if before is None else "moved" if pid in moved_phases and not (fields or steps) else \
            "changed" if fields or steps or pid in moved_phases else "same"
        if status == "same":
            same_phases += 1
        if before is None:
            tally("phase added")
        n_fields += len(fields)
        tally("phase moved", int(pid in moved_phases))
        phases.append({"id": pid, "title": ph.get("title"), "status": status, "moved": pid in moved_phases,
                       "fields": fields, "steps": steps, "same_steps": same})
    for ph in old_phases:
        if ph.get("id") not in new_by_id:
            tally("phase removed")
            phases.append({"id": ph.get("id"), "title": ph.get("title"), "status": "removed", "moved": False,
                           "fields": [], "same_steps": [],
                           "steps": [line(s) | {"status": "removed", "step": s, "fields": []}
                                     for s in ph["step"] if s.get("id") not in new_steps]})
    count = n_fields + sum(moves.values())
    return {"available": True, "kind": "plan", "phases": phases, "same_phases": same_phases, "count": count,
            "summary": _summary(n_fields, n_steps, moves)}


# ---- summaries ---------------------------------------------------------------- #

@dataclass
class Summary:
    item: Item
    answers: dict
    steps_total: int = 0
    steps_done: int = 0
    by_owner: dict = field(default_factory=dict)        # owner -> [done, total]
    for_user: list = field(default_factory=list)       # (what, local, text)
    for_agent: list = field(default_factory=list)
    next_step: dict | None = None
    open_steps: list = field(default_factory=list)     # every step not done or dropped

    def as_json(self) -> dict:
        it = self.item
        return {"id": it.id, "kind": it.kind, "title": it.title, "folder": it.folder, "file": it.rel,
                "summary": it.data.get("summary", ""),
                "steps": {"done": self.steps_done, "total": self.steps_total, "by": self.by_owner},
                "for_user": [dict(zip(("what", "local", "text"), x)) for x in self.for_user],
                "for_agent": [dict(zip(("what", "local", "text"), x)) for x in self.for_agent],
                "next": self.next_step.get("id") if self.next_step else None,
                "open": self.open_steps,
                "paused": paused(self.answers)}


def paused(ans: dict) -> dict | None:
    """Who paused the plan in its page, and when; None while it may be worked
    (p-pw7ft8 s6-1). The agent finishes the step it is on, then waits."""
    p = ans.get("paused")
    return p if isinstance(p, dict) and p.get("at") else None


def plan_approval_due(item: Item, ans: dict, required: bool) -> bool:
    """Does a draft plan wait for the user's approval of its phases and steps?"""
    if not required or item.kind != "plan" or item.folder != "draft" or not item.phases():
        return False
    if any(not d.get("resolved") for d in item.decisions()):
        return False
    return not plan_approval(item, ans).ok


def summarize(item: Item, ans: dict, *, plan_approval_required: bool = True,
              ids: dict | None = None) -> Summary:
    s = Summary(item=item, answers=ans)
    for dec in item.decisions():
        local = decision_local(item, dec)
        st = decision_state(dec, ans, local)
        if st == "open":
            s.for_user.append(("decision", local, dec.get("question", "")))
        elif st == "answered":
            s.for_agent.append(("answer", local, dec.get("question", "")))

    ticks = ans.get("steps", {})
    for step in item.steps():
        sid, owner, state = step.get("id", ""), step.get("by", "agent"), step.get("state", "open")
        done_pair = s.by_owner.setdefault(owner, [0, 0])
        s.steps_total += 1
        done_pair[1] += 1
        if state in DONE_STEP:
            s.steps_done += 1
            done_pair[0] += 1
        elif s.next_step is None and state in ("open", "active"):
            s.next_step = step
        ticked = ticks.get(sid, {}).get("state") == "done"
        if owner == "user" and ticked and state != "done":
            s.for_agent.append(("tick", sid, f"{step.get('title', '')}: done in the page, not yet in the file"))
        if state not in DONE_STEP:
            waits = waiting_on(step, item, ids)
            approval = step_approval(step, ans).status if step.get("gate") else None
            s.open_steps.append({"id": sid, "title": step.get("title", ""), "by": owner, "state": state,
                                 "gate": bool(step.get("gate")), "approval": approval,
                                 "ready": not waits, "waiting_on": waits,
                                 "ticked": owner == "user" and ticked})
            # The user's own steps that can be done now are theirs to do.
            if owner == "user" and not waits and not ticked:
                s.for_user.append(("step", sid, step.get("title", "")))
        if step.get("gate") and state not in DONE_STEP:
            a = step_approval(step, ans)
            if a.status == "none":
                s.for_user.append(("approval", sid, step.get("title", "")))
            elif a.status == "stale":
                s.for_user.append(("approval", sid, f"{step.get('title', '')} (changed since your "
                                                    f"{a.verdict} answer)"))
            elif a.status == "disapproved":
                s.for_agent.append(("disapproved", sid, f"{step.get('title', '')}: drop it or rewrite it"
                                    + (f"; note: {a.note}" if a.note else "")))

    if plan_approval_due(item, ans, plan_approval_required):
        pa = plan_approval(item, ans)
        text = "the plan's phases and steps" + (" (changed since your answer)" if pa.status == "stale" else "")
        s.for_user.append(("plan-approval", "", text))
    elif item.kind == "plan" and item.folder != "draft" and plan_approval_required:
        pa = plan_approval(item, ans)
        if pa.status == "stale":
            s.for_user.append(("plan-approval", "", "the steps changed since you approved the plan; "
                                                    "look again (the agent is not blocked)"))

    for c in ans.get("comments", []):
        # Answered (`waterrise reply`) or handled (`resolve`): it waits on the
        # user now, who writes again to go on (p-pw7ft8, decision `chat`).
        if not c.get("resolved") and not c.get("replies"):
            s.for_agent.append(("comment", str(c.get("id", "")),
                                (f"on {c['on']}: " if c.get("on") else "") + str(c.get("text", ""))))

    if item.kind == "review":
        for f in item.findings():
            if f.get("state", "open") == "open":
                s.for_agent.append(("finding", f.get("id", ""), f.get("title", "")))
    return s
