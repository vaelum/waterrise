"""The work-plan MCP server: what counts as work, when a wait ends, the protocol.

Most of it runs no waterrise: `status` is replaced by canned `waterrise status
--json` output, shaped like waterrise/state.py's. The last tests run the real
one, through `WATERRISE` pointed at this checkout.
"""

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from waterrise import mcp as workplan
from waterrise.mcp import server

REPO = Path(__file__).resolve().parent.parent


def _status(folder="draft", for_user=(), for_agent=(), open_steps=(), done=0, total=3):
    return {"id": "p-abc123", "kind": "plan", "title": "A plan", "folder": folder,
            "file": f"{folder}/a.toml",
            "steps": {"done": done, "total": total, "by": {}},
            "for_user": [dict(zip(("what", "local", "text"), x)) for x in for_user],
            "for_agent": [dict(zip(("what", "local", "text"), x)) for x in for_agent],
            "next": None, "open": list(open_steps)}


def _step(sid, by="agent", ready=True, gate=False, approval=None, state="open"):
    return {"id": sid, "title": f"step {sid}", "by": by, "state": state, "gate": gate,
            "approval": approval, "ready": ready, "waiting_on": [], "ticked": False}


# --- assess ---------------------------------------------------------------- #


def test_an_open_decision_is_waiting_on_amos():
    a = workplan.assess(_status(for_user=[("decision", "how", "How?")],
                                open_steps=[_step("s1-1", ready=False)]))
    assert a.verdict == "waiting"
    assert a.user == ["decision how: How?"]


def test_anything_for_the_agent_is_work():
    a = workplan.assess(_status(for_agent=[("comment", "c1", "on s1-1: shorter")],
                                for_user=[("decision", "x", "?")]))
    assert a.verdict == "work"
    assert a.agent == ["comment c1: on s1-1: shorter"]


def test_a_ready_step_without_a_gate_is_work():
    st = _status(folder="in-progress", open_steps=[_step("s1-1")])
    assert workplan.assess(st).verdict == "work"


@pytest.mark.parametrize("approval", ["none", "stale"])
def test_a_ready_gated_step_waits_for_its_approval(approval):
    st = _status(folder="in-progress", open_steps=[_step("s1-1", gate=True, approval=approval)],
                 for_user=[("approval", "s1-1", "deploy")])
    assert workplan.assess(st).verdict == "waiting"


def test_an_approved_gate_is_work_and_says_to_check_it():
    st = _status(folder="in-progress", open_steps=[_step("s1-1", gate=True, approval="approved")])
    a = workplan.assess(st)
    assert a.verdict == "work"
    assert "planning gate" in a.agent[0]


def test_a_user_step_is_not_the_agents_work():
    st = _status(folder="in-progress", open_steps=[_step("s1-1", by="user")],
                 for_user=[("step", "s1-1", "open the site")])
    assert workplan.assess(st).verdict == "waiting"


def test_a_blocked_step_is_not_work():
    st = _status(folder="in-progress", open_steps=[_step("s1-1", state="blocked")])
    assert workplan.assess(st).verdict == "waiting"


def test_an_approved_draft_is_work_the_move_to_todo():
    st = _status(folder="draft", open_steps=[_step("s1-1", ready=False)])
    a = workplan.assess(st)
    assert a.verdict == "work"
    assert "todo" in a.agent[0]


def test_a_drafts_steps_are_not_started_before_its_decisions():
    st = _status(folder="draft", for_user=[("decision", "how", "How?")],
                 open_steps=[_step("s1-1", ready=True)])
    assert workplan.assess(st).verdict == "waiting"


def test_a_draft_waiting_for_plan_approval_is_waiting():
    st = _status(folder="draft", for_user=[("plan-approval", "", "the plan's phases and steps")],
                 open_steps=[_step("s1-1", ready=True)])
    assert workplan.assess(st).verdict == "waiting"   # ready, but not approved


def test_all_steps_done_is_the_move_to_done():
    a = workplan.assess(_status(folder="in-progress", done=3, total=3))
    assert a.verdict == "work"
    assert "done" in a.agent[-1]


def test_done_folder_is_done():
    assert workplan.assess(_status(folder="done", done=3, total=3)).verdict == "done"


# --- wait ------------------------------------------------------------------ #


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


@pytest.fixture()
def fake(monkeypatch, tmp_path):
    """`resolve` and `status` replaced; `seq` is the statuses in order."""
    plan = workplan.Plan(repo=tmp_path, id="p-abc123")
    seq: list = []
    calls = {"n": 0}
    monkeypatch.setattr(workplan, "resolve", lambda spec, cwd=None: plan)
    monkeypatch.setattr(workplan, "page_url", lambda p: "http://127.0.0.1:8765/r/x/i/p-abc123")

    def status(p):
        calls["n"] += 1
        item = seq[min(calls["n"], len(seq)) - 1]
        if isinstance(item, Exception):
            raise item
        return item
    monkeypatch.setattr(workplan, "status", status)
    return seq


WAITING = _status(for_user=[("decision", "how", "How?")], open_steps=[_step("s1-1", ready=False)])


def test_wait_returns_at_once_when_there_is_work(fake):
    fake += [_status(for_agent=[("answer", "how", "How?")])]
    clock = Clock()
    out = workplan.tool_wait("p-abc123", None, 5, clock=clock, sleep=clock.sleep, poll=10)
    assert out.startswith("NOT WAITING")
    assert clock.t == 0


def test_wait_ends_when_amos_answers(fake):
    fake += [WAITING, WAITING, _status(for_agent=[("answer", "how", "How?")])]
    clock = Clock()
    out = workplan.tool_wait("p-abc123", None, 5, clock=clock, sleep=clock.sleep, poll=10)
    assert out.startswith("AMOS ACTED")
    assert "answer how" in out
    assert clock.t == 20


def test_wait_ends_on_a_change_that_brings_no_work(fake):
    later = _status(for_user=[("decision", "how", "How?")],
                    open_steps=[_step("s1-1", ready=False, gate=True, approval="approved")])
    fake += [WAITING, later]
    clock = Clock()
    out = workplan.tool_wait("p-abc123", None, 5, clock=clock, sleep=clock.sleep, poll=10)
    assert out.startswith("CHANGED")


def test_wait_times_out_with_nothing_yet(fake):
    fake += [WAITING]
    clock = Clock()
    out = workplan.tool_wait("p-abc123", None, 1, clock=clock, sleep=clock.sleep, poll=15)
    assert out.startswith("NOTHING YET")
    assert clock.t == 60


def test_wait_survives_a_failing_status_until_the_third(fake):
    err = workplan.PlanError("half-written file")
    fake += [WAITING, err, WAITING, err, err, err]
    clock = Clock()
    out = workplan.tool_wait("p-abc123", None, 5, clock=clock, sleep=clock.sleep, poll=10)
    assert out.startswith("ERROR")
    assert clock.t == 50


def test_wait_stops_when_cancelled(fake):
    fake += [WAITING]
    clock = Clock()
    out = workplan.tool_wait("p-abc123", None, 5, cancelled=lambda: clock.t >= 20,
                             clock=clock, sleep=clock.sleep, poll=10)
    assert out.startswith("CANCELLED")


# --- resolve --------------------------------------------------------------- #


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    r = tmp_path / "proj"
    (r / "planning" / "draft").mkdir(parents=True)
    (r / "planning" / "draft" / "a.toml").write_text('kind = "plan"\nid = "p-abc123"\ntitle = "A"\n')
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    state = tmp_path / "state"
    (state / "waterrise").mkdir(parents=True)
    (state / "waterrise" / "registry.json").write_text(
        json.dumps({"repos": [{"name": "proj", "path": str(r)}]}))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    return r


def test_resolve_a_bare_id_through_the_registry(repo, tmp_path):
    assert workplan.resolve("p-abc123", tmp_path) == workplan.Plan(repo=repo, id="p-abc123")


def test_resolve_repo_and_id(repo):
    assert workplan.resolve("proj:p-abc123").repo == repo


def test_resolve_a_file(repo):
    p = workplan.resolve(str(repo / "planning" / "draft" / "a.toml"))
    assert p == workplan.Plan(repo=repo, id="p-abc123")


def test_a_deploy_clone_of_the_same_repository_is_not_an_ambiguity(repo, tmp_path, monkeypatch):
    clone = tmp_path / "deploy"
    subprocess.run(["git", "clone", "-q", str(repo), str(clone)], check=True, capture_output=True)
    (clone / "planning" / "draft").mkdir(parents=True, exist_ok=True)
    (clone / "planning" / "draft" / "a.toml").write_text('kind = "plan"\nid = "p-abc123"\n')
    reg = tmp_path / "state" / "waterrise" / "registry.json"
    reg.write_text(json.dumps({"repos": [{"name": "proj", "path": str(repo)},
                                         {"name": "proj", "path": str(clone)}]}))
    assert workplan.resolve("p-abc123").repo == repo
    assert workplan.resolve("p-abc123", clone).repo == clone
    assert workplan.resolve(f"{clone}:p-abc123").repo == clone


def test_resolve_an_unknown_id_says_so(repo):
    with pytest.raises(workplan.PlanError, match="no item p-zzzzzz"):
        workplan.resolve("p-zzzzzz")


def test_resolve_an_unknown_repo_lists_the_known_ones(repo):
    with pytest.raises(workplan.PlanError, match="known: proj"):
        workplan.resolve("nope:p-abc123")


# --- the protocol ---------------------------------------------------------- #


def _talk(*msgs, cwd=None):
    out = io.StringIO()
    s = server.Server(out=out, cwd=cwd)
    s.serve(io.StringIO("".join(json.dumps(m) + "\n" for m in msgs)))
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_initialize_lists_tools_and_prompts():
    init, tools, prompts = _talk(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "prompts/list"})
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["serverInfo"]["name"] == "work-plan"
    assert {t["name"] for t in tools["result"]["tools"]} == {"plan_status", "wait_for_plan"}
    assert prompts["result"]["prompts"][0]["name"] == "work_plan"


def test_the_prompt_names_the_plan_and_the_loop():
    (got,) = _talk({"jsonrpc": "2.0", "id": 1, "method": "prompts/get",
                    "params": {"name": "work_plan", "arguments": {"plan": "cac:p-zha4jf"}}})
    text = got["result"]["messages"][0]["content"]["text"]
    assert "cac:p-zha4jf" in text
    assert "wait_for_plan" in text and "waterrise gate" in text and "planning/answers/" in text


def test_an_unknown_method_is_an_error():
    (got,) = _talk({"jsonrpc": "2.0", "id": 9, "method": "resources/list"})
    assert got["error"]["code"] == -32601


def test_a_tool_error_is_returned_to_the_agent(monkeypatch):
    def boom(spec, cwd):
        raise workplan.PlanError("no item p-zzzzzz")
    monkeypatch.setattr(workplan, "tool_status", boom)
    out = io.StringIO()
    s = server.Server(out=out)
    s.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
              "params": {"name": "plan_status", "arguments": {"plan": "p-zzzzzz"}}})
    import time
    for _ in range(100):
        if out.getvalue():
            break
        time.sleep(0.01)
    got = json.loads(out.getvalue())
    assert got["result"]["isError"] is True
    assert "no item p-zzzzzz" in got["result"]["content"][0]["text"]


# --- running it ------------------------------------------------------------ #


def test_the_server_starts_on_a_bare_interpreter():
    path = REPO / "waterrise" / "mcp" / "server.py"
    msg = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n"
    out = subprocess.run(["python3", "-I", str(path)], input=msg, capture_output=True,
                         text=True, timeout=30)
    assert json.loads(out.stdout) == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_the_work_plan_skill_inlines_the_servers_prompt():
    # skills/work-plan is `/work-plan <plan>`; it must print the prompt's text,
    # reaching the server from the skill's directory, not keep a copy of it.
    root = REPO
    skill = (root / "skills" / "work-plan" / "SKILL.md").read_text()
    assert '"${CLAUDE_SKILL_DIR}/../../waterrise/mcp/server.py" --prompt "$ARGUMENTS"' in skill
    out = subprocess.run(["python3", "-I", str(root / "skills" / "work-plan" / ".." / ".."
                                              / "waterrise" / "mcp" / "server.py"), "--prompt", "cac:p-zha4jf"],
                         capture_output=True, text=True, timeout=30, check=True)
    assert out.stdout == server.prompt_text("cac:p-zha4jf")


def test_plan_status_runs_waterrise_for_a_project_with_no_butler(tmp_path, monkeypatch):
    """The real chain: the server's status asks `waterrise status --json` in a
    project that has no butler.py, and reads a plan its registry points at."""
    proj = tmp_path / "proj"
    (proj / "plans" / "draft").mkdir(parents=True)
    (proj / "plans" / "planning.toml").write_text("format = 1\nplan_approval = true\n")
    (proj / "plans" / "draft" / "a.toml").write_text(
        'kind = "plan"\nid = "p-abc123"\ntitle = "A"\nsummary = "s"\n'
        'created = 2026-10-18\nupdated = 2026-10-18\nwhy = "w"\n')
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    state = tmp_path / "state"
    (state / "waterrise").mkdir(parents=True)
    (state / "waterrise" / "registry.json").write_text(json.dumps(
        {"repos": [{"name": "proj", "path": str(proj), "planning": str(proj / "plans"), "slug": "proj"}]}))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("WATERRISE", str(REPO))
    plan = workplan.resolve("proj:p-abc123")
    st = workplan.status(plan)
    assert st["id"] == "p-abc123" and st["folder"] == "draft"


def test_the_server_answers_plan_status_with_no_butler_on_the_path(tmp_path):
    proj = tmp_path / "proj"
    (proj / "planning" / "draft").mkdir(parents=True)
    (proj / "planning" / "planning.toml").write_text("format = 1\n")
    (proj / "planning" / "draft" / "a.toml").write_text(
        'kind = "plan"\nid = "p-abc123"\ntitle = "A plan"\nsummary = "s"\n'
        'created = 2026-10-18\nupdated = 2026-10-18\nwhy = "w"\n')
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("WATERRISE", "PYTHON"))}
    # No service to start: `serve` fails, and the reply carries no page.
    env |= {"XDG_STATE_HOME": str(tmp_path / "state"), "WATERRISE": str(REPO),
            "WATERRISE_PORT": "1"}
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "plan_status", "arguments": {"plan": str(proj / "planning" / "draft" / "a.toml")}}}]
    proc = subprocess.Popen([sys.executable, "-I", str(REPO / "waterrise" / "mcp" / "server.py")],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env, cwd=proj)
    for m in msgs:
        proc.stdin.write(json.dumps(m) + "\n")
    proc.stdin.flush()
    replies = {}
    while len(replies) < 3:
        line = proc.stdout.readline()
        if not line:
            break
        msg = json.loads(line)
        replies[msg.get("id")] = msg
    proc.stdin.close()
    proc.wait(timeout=10)
    text = replies[3]["result"]["content"][0]["text"]
    assert replies[3]["result"]["isError"] is False, text
    assert "p-abc123" in text and "A plan" in text
