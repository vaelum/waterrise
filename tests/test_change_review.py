"""What changed since an answer: the comparison, and an answer on outdated text."""

import json

import pytest

from tests.test_planning import plan_text, run
from tests.test_planning_service import repo, req, server, state  # noqa: F401 (fixtures)
from waterrise import answers, types
from waterrise import state as st


def step(**more) -> dict:
    return {"id": "s1-1", "title": "A title", "by": "agent", **more}


def phase(pid="p1", title="One", steps=(), goal=None) -> dict:
    return {"id": pid, "title": title, "goal": goal, "step": [types.step_snapshot(s) for s in steps]}


def only_field(changes: dict) -> dict:
    fields = [f for ph in changes["phases"] for s in ph["steps"] for f in s["fields"]]
    assert len(fields) == 1
    return fields[0]


def test_one_changed_word_marks_that_word():
    long = "the suite passes on linux and on macos in the morning before the release goes out"
    old = [phase(steps=[step(details=long)])]
    new = [phase(steps=[step(details=long.replace("macos", "windows"))])]
    c = st.compare_plans(old, new)
    runs = only_field(c)["prose"]["runs"]
    assert ["del", "macos"] in runs and ["add", "windows"] in runs
    assert "".join(t for k, t in runs if k != "add") == long
    assert c["count"] == 1 and c["summary"] == "1 field changed in 1 step"


def test_line_breaks_stay_in_the_runs():
    old = "first line\nsecond line\nthird line\nfourth line"
    new = "first line\nsecond words\nthird line\nfourth line"
    assert "".join(t for k, t in st.word_runs(old, new)["runs"] if k != "del") == new


def test_a_rewritten_field_is_old_then_new():
    prose = only_field(st.compare_plans(
        [phase(steps=[step(details="alpha beta gamma delta epsilon zeta")])],
        [phase(steps=[step(details="one two three four five six seven")])]))["prose"]
    assert prose["rewritten"] is True and "runs" not in prose
    assert prose["old"].startswith("alpha") and prose["new"].startswith("one")


@pytest.mark.parametrize("kept,rewritten", [(9, False), (2, True)])
def test_the_threshold(kept, rewritten):
    old = " ".join(f"w{i}" for i in range(10))
    new = " ".join([f"w{i}" for i in range(kept)] + [f"x{i}" for i in range(10 - kept)])
    assert st.word_runs(old, new)["rewritten"] is rewritten


def test_words_only_added_or_only_cut_are_marked_not_rewritten():
    assert st.word_runs("Deploy", "Deploy it all to the host")["rewritten"] is False
    assert st.word_runs("Deploy it all to the host", "Deploy")["rewritten"] is False


def statuses(c: dict) -> dict:
    return {s["id"]: s["status"] for ph in c["phases"] for s in ph["steps"]}


def test_a_step_added_removed_and_moved_by_id():
    a, b, d = step(id="a"), step(id="b"), step(id="d")
    c = st.compare_plans([phase(steps=[a, b]), phase("p2", "Two", [d])],
                            [phase(steps=[step(id="n"), a]), phase("p2", "Two", [b])])
    assert statuses(c) == {"n": "added", "b": "moved", "d": "removed"}
    moved = next(s for ph in c["phases"] for s in ph["steps"] if s["id"] == "b")
    assert moved["from_phase"] == "p1"
    assert c["summary"] == "1 step added, 1 step removed, 1 step moved"


def test_a_step_moved_within_its_phase():
    a, b, d = step(id="a"), step(id="b"), step(id="d")
    c = st.compare_plans([phase(steps=[a, b, d])], [phase(steps=[d, a, b])])
    assert statuses(c) == {"d": "moved"}


def test_a_changed_phase_title_and_what_did_not_change():
    a, b = step(id="a"), step(id="b")
    c = st.compare_plans([phase(steps=[a, b]), phase("p2", "Two", [step(id="d")])],
                            [phase(title="Won", steps=[a, b]), phase("p2", "Two", [step(id="d")])])
    first, second = c["phases"]
    assert first["status"] == "changed" and [f["field"] for f in first["fields"]] == ["title"]
    assert [s["id"] for s in first["same_steps"]] == ["a", "b"] and first["steps"] == []
    assert second["status"] == "same" and c["same_phases"] == 1


def test_an_added_and_a_removed_phase():
    c = st.compare_plans([phase("p1", "One", [step(id="a")])], [phase("p2", "Two", [step(id="b")])])
    assert {ph["id"]: ph["status"] for ph in c["phases"]} == {"p2": "added", "p1": "removed"}
    assert c["summary"] == "1 phase added, 1 phase removed"
    assert statuses(c) == {"b": "added", "a": "removed"}


def test_a_missing_snapshot_is_unavailable_not_nothing():
    for c in (st.compare_plans(None, []), st.compare_steps(None, step())):
        assert c["available"] is False and "unavailable" in c["summary"]


def test_a_plan_with_no_phases_compares():
    c = st.compare_plans([], [])
    assert c["available"] is True and c["phases"] == [] and c["count"] == 0


def test_an_older_snapshot_does_not_show_defaults_as_changes():
    # As an older version recorded it, without fields added and defaulted since.
    recorded = {"id": "s1-1", "title": "A title", "by": "agent"}
    now = types.step_snapshot(step())
    assert st.compare_steps(recorded, now)["fields"] == []
    c = st.compare_plans([{"id": "p1", "title": "One", "goal": None, "step": [recorded]}], [phase(steps=[step()])])
    assert c["count"] == 0 and c["phases"][0]["status"] == "same"


def test_a_steps_comparison_names_its_fields():
    c = st.compare_steps(types.step_snapshot(step(by="agent")), types.step_snapshot(step(by="user")))
    assert [(f["field"], f["old"], f["new"]) for f in c["fields"]] == [("by", "agent", "user")]
    assert c["summary"] == "1 field changed"


def test_it_copes_with_a_plan_this_size():
    long = " ".join(f"word{i}" for i in range(300))
    steps = [step(id=f"s{i}", details=long) for i in range(32)]
    changed = [step(id=f"s{i}", details=long.replace("word7 ", "changed ")) for i in range(32)]
    assert st.compare_plans([phase(steps=steps)], [phase(steps=changed)])["count"] == 32


# ---- the page's route --------------------------------------------------------------- #

@pytest.fixture
def stale(server, repo):
    """A plan with a gated step, approved and then edited."""
    return server, repo, repo / "planning" / "draft" / "a.toml"


def view(port, plan="p-4k7x2m"):
    return req(port, "GET", f"/api/r/demo/item/{plan}")[1]


def answers_of(repo, plan="p-4k7x2m"):
    return answers.read(repo / "planning" / "answers" / f"{plan}.json", plan)


def edit(path):
    text = path.read_text(encoding="utf-8")
    assert 'title = "Deploy"' in text
    path.write_text(text.replace('title = "Deploy"', 'title = "Deploy it all"'), encoding="utf-8")


def test_the_current_hash_is_saved_an_older_one_refused_and_none_still_works(stale):
    port, repo, path = stale
    url = "/api/r/demo/approval/p-4k7x2m/s1-2"
    shown = view(port)["approvals"]["s1-2"]["current"]
    assert req(port, "PUT", url, {"verdict": "approved", "sha256": shown})[0] == 200
    assert answers_of(repo)["approvals"]["s1-2"]["sha256"] == shown
    edit(path)
    before = (repo / "planning" / "answers" / "p-4k7x2m.json").read_bytes()
    status, body, _ = req(port, "PUT", url, {"verdict": "approved", "sha256": shown})
    assert status == 409 and "changed" in json.dumps(body)
    assert (repo / "planning" / "answers" / "p-4k7x2m.json").read_bytes() == before
    assert req(port, "PUT", url, {"verdict": "disapproved", "sha256": shown})[0] == 409
    assert req(port, "PUT", url, {"verdict": None, "sha256": shown})[0] == 200
    assert req(port, "PUT", url, {"verdict": "approved"})[0] == 200
    assert answers_of(repo)["approvals"]["s1-2"]["sha256"] != shown


def test_the_plans_answer_is_refused_the_same_way(stale):
    port, repo, path = stale
    url = "/api/r/demo/approval/p-4k7x2m"
    shown = view(port)["plan_approval"]["current"]
    edit(path)
    assert req(port, "PUT", url, {"verdict": "approved", "sha256": shown})[0] == 409
    assert "plan_approval" not in answers_of(repo)
    assert req(port, "PUT", url, {"verdict": "approved", "sha256": 7})[0] == 400
    assert req(port, "PUT", url, {"verdict": "approved",
                                  "sha256": view(port)["plan_approval"]["current"]})[0] == 200


def test_the_view_carries_changes_and_not_the_snapshots(stale):
    port, repo, path = stale
    for url in ("/api/r/demo/approval/p-4k7x2m/s1-2", "/api/r/demo/approval/p-4k7x2m"):
        assert req(port, "PUT", url, {"verdict": "approved"})[0] == 200
    current = view(port)
    assert "changes" not in current["approvals"]["s1-2"] and "snapshot" not in current["plan_approval"]
    edit(path)
    v = view(port)
    a, pa = v["approvals"]["s1-2"], v["plan_approval"]
    assert a["status"] == "stale" and pa["status"] == "stale"
    assert "snapshot" not in a and "now" not in a and "snapshot" not in pa and "now" not in pa
    assert a["sha256"] != a["current"]
    assert a["changes"]["fields"][0]["field"] == "title"
    assert a["changes"]["fields"][0]["prose"]["rewritten"] is False
    assert any(k == "add" and "it all" in x for k, x in a["changes"]["fields"][0]["prose"]["runs"])
    assert pa["changes"]["count"] == 1
