"""The service keeps what a page reads while nothing behind it changes, and never
longer (p-pw7ft8 s3-3)."""

import json

import pytest

from tests.test_planning import plan_text, run
from tests.test_worktrees import entry, env, git, register, repo, wt_folder, wtf  # noqa: F401 (fixtures)
from waterrise import answers, check, service


@pytest.fixture
def page(repo, capsys):
    """`repo` with a plan p-4k7x2m in todo/, committed, a worktree foo, registered."""
    (repo / "planning" / "todo" / "m.toml").write_text(plan_text(), encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "a plan")
    assert run(repo, "worktree", "add", "foo") == 0
    e = entry(repo)
    register(e)
    service.forget_views()
    capsys.readouterr()
    return repo, e


def titles(view):
    return {i["id"]: i["title"] for i in view["items"] if i.get("id")}


def test_nothing_changed_is_the_same_read(page):
    repo, e = page
    assert service._folder_view(e) is service._folder_view(e)


def test_an_edited_item_shows_at_once(page):
    repo, e = page
    service.repo_view(e)
    path = repo / "planning" / "todo" / "m.toml"
    path.write_text(path.read_text(encoding="utf-8").replace('title = "Monitoring"', 'title = "Watching"'),
                    encoding="utf-8")
    assert titles(service.repo_view(e))["p-4k7x2m"] == "Watching"
    assert service.item_view(e, "p-4k7x2m")["data"]["title"] == "Watching"


def test_an_answer_shows_at_once(page):
    repo, e = page
    before = service.item_view(e, "p-4k7x2m")
    path = repo / "planning" / "answers" / "p-4k7x2m.json"
    path.parent.mkdir(exist_ok=True)
    answers.update(path, "p-4k7x2m", lambda a: a.setdefault("comments", []).append(
        {"id": "c1", "on": "", "text": "hello", "at": answers.now(), "by": "u"}))
    after = service.item_view(e, "p-4k7x2m")
    assert json.dumps(before, sort_keys=True, default=str) != json.dumps(after, sort_keys=True, default=str)


def test_a_protocol_shows_in_the_check_at_once(page):
    repo, e = page
    first = service.repo_view(e)
    proto = repo / "planning" / "protocols" / "p-4k7x2m"
    proto.mkdir(parents=True)
    (proto / "s1-1.md").write_text("2026-11-10 note\nok\n", encoding="utf-8")
    clean = service.repo_view(e)["errors"]
    with (proto / "s1-1.md").open("a", encoding="utf-8") as fh:
        fh.write("GITHUB_TOKEN=ghp_" + "a" * 36 + "\n")
    assert service.repo_view(e)["errors"] == clean + 1 == first["errors"] + 1
    key = service._view_key(e)
    rep = service._check_view(e, service._folder_view(e, key), key)
    assert any("credential" in f.message for f in rep.of("error"))


def test_a_growing_protocol_keeps_the_read_and_the_check(page, monkeypatch):
    """An agent's run appends to a protocol on every line of output: that must not
    read the folder or check it again on every request (2026-11-18: the plan page
    of a project with a run going took 2.5 s a load, every half second)."""
    repo, e = page
    proto = repo / "planning" / "protocols" / "p-4k7x2m"
    proto.mkdir(parents=True)
    (proto / "s1-1.md").write_text("\n## 2026-11-10 12:00:00 note\nok\n", encoding="utf-8")
    service.item_view(e, "p-4k7x2m")
    folder = service._folder_view(e)
    runs = []
    real = check.run
    monkeypatch.setattr(check, "run", lambda *a, **k: runs.append(1) or real(*a, **k))
    with (proto / "s1-1.md").open("a", encoding="utf-8") as fh:
        fh.write("a line of output\n")
    view = service.item_view(e, "p-4k7x2m")
    assert service._folder_view(e) is folder
    assert runs == []
    assert view["protocols"]["s1-1"]["entries"] == 1, "the protocols themselves are read fresh"


def test_a_plan_changed_in_a_worktree_shows_at_once(page):
    repo, e = page
    assert run(repo, "assign", "p-4k7x2m", "foo") == 0
    service.repo_view(e)
    copy = wtf(repo) / "foo" / "planning" / "todo" / "m.toml"
    copy.write_text(copy.read_text(encoding="utf-8").replace('title = "Monitoring"', 'title = "On foo"'),
                    encoding="utf-8")
    assert titles(service.repo_view(e))["p-4k7x2m"] == "On foo"


def test_what_git_says_is_asked_every_time(page):
    repo, e = page
    trees = {w["name"]: w for w in service.repo_view(e)["worktrees"]}
    assert trees["foo"]["ahead"] == 0
    git(wtf(repo) / "foo", "commit", "--allow-empty", "-qm", "ahead")
    (wtf(repo) / "foo" / "dirt").write_text("x", encoding="utf-8")
    trees = {w["name"]: w for w in service.repo_view(e)["worktrees"]}
    assert trees["foo"]["ahead"] == 1 and trees["foo"]["clean"] is False


def test_a_protocol_scan_is_kept_by_size_and_mtime(tmp_path):
    p = tmp_path / "s.md"
    p.write_text("2026-11-10 note\n", encoding="utf-8")
    assert check._protocol_scan(p) == (True, ())
    p.write_text("nothing\nGITHUB_TOKEN=ghp_" + "b" * 36 + "\n", encoding="utf-8")
    assert check._protocol_scan(p) == (False, (2,))
