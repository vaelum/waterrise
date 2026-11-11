"""The chat beside a plan: whole-plan comments, answered with `waterrise reply`
(p-pw7ft8 s7-3, decision `chat`)."""

import json

from tests.test_planning import run
from tests.test_planning_service import put, repo, server, state  # noqa: F401 (fixtures)
from waterrise import client
from waterrise import mcp as workplan
from waterrise.mcp import server as mcp_server


def send(port, text):
    status, body = client.call(port, "POST", "/api/r/demo/comment/p-4k7x2m", {"on": "", "text": text})
    assert status == 200
    return body["id"]


def agent_work(repo, capsys):
    capsys.readouterr()
    run(repo, "status", "p-4k7x2m", "--json")
    return [x for x in json.loads(capsys.readouterr().out)[0]["for_agent"] if x["what"] == "comment"]


def test_a_message_is_work_until_it_is_answered(server, repo, capsys):
    cid = send(server, "Please split s1-1 in two.")
    assert [x["local"] for x in agent_work(repo, capsys)] == [cid]
    assert run(repo, "reply", "p-4k7x2m", cid, "Will do:", "s1-1a", "and", "s1-1b.") == 0
    assert agent_work(repo, capsys) == [], "answered: it waits on the user now"
    _, item = client.call(server, "GET", "/api/r/demo/item/p-4k7x2m")
    c = next(c for c in item["answers"]["comments"] if c["id"] == cid)
    assert c["replies"][0]["text"] == "Will do: s1-1a and s1-1b." and c["replies"][0]["by"] == "agent"
    assert not c.get("resolved"), "a reply does not mark it handled"
    assert run(repo, "resolve", "p-4k7x2m", cid, "--note", "split as asked") == 0
    _, item = client.call(server, "GET", "/api/r/demo/item/p-4k7x2m")
    c = next(c for c in item["answers"]["comments"] if c["id"] == cid)
    assert c["resolved"]["note"] == "split as asked" and len(c["replies"]) == 1


def test_a_follow_up_is_a_new_message_and_work_again(server, repo, capsys):
    first = send(server, "Rename the plan.")
    run(repo, "reply", "p-4k7x2m", first, "To what?")
    assert agent_work(repo, capsys) == []
    second = send(server, "To Watching.")
    assert [x["local"] for x in agent_work(repo, capsys)] == [second]


def test_reply_refuses_an_unknown_comment_and_empty_text(server, repo, capsys):
    assert run(repo, "reply", "p-4k7x2m", "c99", "hello") == 1
    cid = send(server, "hi")
    assert run(repo, "reply", "p-4k7x2m", cid, " ") == 1


def test_wait_for_plan_returns_a_message_at_once(server, repo, monkeypatch):
    # the real status of the fixture's plan, through the command, as the MCP tools read it
    plan = workplan.Plan(repo=repo, id="p-4k7x2m")
    monkeypatch.setattr(workplan, "resolve", lambda spec, cwd=None: plan)
    monkeypatch.setattr(workplan, "page_url", lambda p: None)
    before = workplan.status(plan)
    send(server, "Add a step for backups.")
    after = workplan.status(plan)
    assert workplan.assess(after).verdict == "work"
    assert any("Add a step for backups." in a for a in workplan.assess(after).agent)
    assert workplan.assess(before).verdict == "waiting"
    out = workplan.tool_wait("p-4k7x2m", None, 1, poll=0.01)
    assert out.startswith("NOT WAITING") and "Add a step for backups." in out, out


def test_the_prompt_says_to_answer_every_chat_message():
    text = mcp_server.prompt_text("p-abc123")
    assert "waterrise reply" in text and "chat beside it" in text
