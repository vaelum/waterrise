"""Pause and continue a plan from its page (p-pw7ft8 s6-1, decision `pause`: after the
current step)."""

import json

from tests.test_planning import plan_text, run
from tests.test_planning_service import put, repo, server, state  # noqa: F401 (fixtures)
from tests.test_workplan import Clock, _status, _step, fake  # noqa: F401 (fixture)
from waterrise import client
from waterrise import mcp as workplan
from waterrise.mcp import server as mcp_server

PAUSED = {"at": "2026-11-11T10:00:00Z", "by": "page"}


def pause(port, item, on):
    return client.call(port, "PUT", f"/api/r/demo/pause/{item}", {"paused": on})


def test_the_page_pauses_and_continues_and_status_says_so(server, repo, capsys):
    status, body = pause(server, "p-4k7x2m", True)
    assert status == 200 and body == {"paused": True}
    capsys.readouterr()
    assert run(repo, "status", "p-4k7x2m", "--json") == 0
    st = json.loads(capsys.readouterr().out)[0]
    assert st["paused"]["by"] == "page" and st["paused"]["at"]
    assert run(repo, "status", "p-4k7x2m") == 0
    assert "paused in the page since" in capsys.readouterr().out
    status, body = pause(server, "p-4k7x2m", False)
    assert status == 200
    run(repo, "status", "p-4k7x2m", "--json")
    assert json.loads(capsys.readouterr().out)[0]["paused"] is None


def test_the_route_takes_only_a_plan_and_a_boolean(server, repo):
    assert pause(server, "p-4k7x2m", "yes")[0] == 400
    assert pause(server, "p-zzzzzz", True)[0] == 404


def test_paused_with_nothing_active_says_wait():
    a = workplan.assess(_status(folder="in-progress", open_steps=[_step("s1-1")]) | {"paused": PAUSED})
    assert a.verdict == "paused" and a.agent == []
    assert "press Continue" in a.user[0]


def test_paused_with_an_active_step_says_finish_it_and_start_nothing():
    st = _status(folder="in-progress", open_steps=[_step("s1-1", state="active"), _step("s1-2")])
    a = workplan.assess(st | {"paused": PAUSED})
    assert a.verdict == "paused"
    assert len(a.agent) == 1 and "s1-1" in a.agent[0] and "start nothing else" in a.agent[0]


def test_plan_status_says_paused(fake, monkeypatch):
    fake += [_status(folder="in-progress", open_steps=[_step("s1-1")]) | {"paused": PAUSED}]
    assert workplan.tool_status("p-abc123", None).startswith("PAUSED")


def test_wait_holds_while_paused_and_returns_when_continued(fake):
    paused = _status(folder="in-progress", open_steps=[_step("s1-1")]) | {"paused": PAUSED}
    fake += [paused, paused, _status(folder="in-progress", open_steps=[_step("s1-1")]) | {"paused": None}]
    clock = Clock()
    out = workplan.tool_wait("p-abc123", None, 5, clock=clock, sleep=clock.sleep, poll=10)
    assert out.startswith("CONTINUED") and clock.t == 20


def test_wait_says_paused_when_the_user_pauses_meanwhile(fake):
    waiting = _status(for_user=[("decision", "how", "How?")], open_steps=[_step("s1-1", ready=False)])
    fake += [waiting, waiting | {"paused": PAUSED}]
    clock = Clock()
    assert workplan.tool_wait("p-abc123", None, 5, clock=clock, sleep=clock.sleep, poll=10).startswith("PAUSED")


def test_the_prompt_says_what_to_do_when_paused():
    text = mcp_server.prompt_text("p-abc123")
    assert "PAUSED" in text and "finish the step you are on" in text and "CONTINUED" in text


def test_the_repository_page_knows_which_plans_are_paused(server, repo):
    """The lanes' cards show it and toggle it (amos, c2 on p-pw7ft8)."""
    pause(server, "p-4k7x2m", True)
    _, view = client.call(server, "GET", "/api/r/demo")
    card = next(i for i in view["items"] if i.get("id") == "p-4k7x2m")
    assert card["paused"]["by"] == "page"
    pause(server, "p-4k7x2m", False)
    _, view = client.call(server, "GET", "/api/r/demo")
    assert next(i for i in view["items"] if i.get("id") == "p-4k7x2m")["paused"] is None
