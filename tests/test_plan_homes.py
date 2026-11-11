"""A plan tracked in the repository can live in a worktree, on its branch."""

import json
import sys
from pathlib import Path

import pytest

from tests.test_planning import run
from tests.test_worktrees import git, set_folder, wt_folder, wtf  # noqa: F401 (wt_folder is a fixture)
from waterrise import answers, central, client, folder

PLAN = '''#:schema ../.schema/v1.json
kind = "plan"
id = "{id}"
title = "{title}"
summary = "A plan."
created = 2026-11-03
updated = 2026-11-03
why = "Because."

[[phase]]
id = "p1"
title = "Work"

  [[phase.step]]
  id = "s1"
  title = "Do it"
  by = "agent"
  gate = true
'''


@pytest.fixture
def project(tmp_path, monkeypatch, wt_folder):
    """A repository with a committed plan, assigned to `foo`, and worktrees foo and bar."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    for name in ("WATERRISE_PROJECT", "WATERRISE_PLANNING_DIR", "WATERRISE_PROJECT_NAME", "WATERRISE_PROG"):
        monkeypatch.delenv(name, raising=False)
    root = (tmp_path / "repo").resolve()
    root.mkdir()
    git(root, "init", "-q")
    assert run(root, "init") == 0
    set_folder(root, wt_folder)
    write_plan(root, "p-aaaaaa", "assigned", "On main")
    write_plan(root, "p-bbbbbb", "unassigned", "Stays on main")
    git(root, "add", ".")
    git(root, "commit", "-qm", "first")
    for name in ("foo", "bar"):
        assert run(root, "worktree", "add", name) == 0
    assert run(root, "assign", "p-aaaaaa", "foo") == 0
    write_plan(wtf(root) / "foo", "p-aaaaaa", "assigned", "On the branch")
    write_plan(wtf(root) / "foo", "p-cccccc", "only-here", "Only on foo")
    return root


def write_plan(checkout: Path, item_id: str, name: str, title: str) -> Path:
    path = checkout / "planning" / "draft" / f"{name}.toml"
    path.write_text(PLAN.format(id=item_id, title=title), encoding="utf-8", newline="\n")
    return path


def places(root: Path) -> list[Path]:
    return [root, wtf(root) / "foo", wtf(root) / "bar"]


@pytest.mark.parametrize("where", [0, 1, 2])
@pytest.mark.parametrize("item_id,title", [("p-aaaaaa", "On the branch"), ("p-bbbbbb", "Stays on main"),
                                           ("p-cccccc", "Only on foo")])
def test_every_place_reads_the_same_copy(project, capsys, where, item_id, title):
    here = places(project)[where]
    capsys.readouterr()
    assert run(here, "show", item_id, "--json") == 0
    assert json.loads(capsys.readouterr().out)["data"]["title"] == title
    assert run(here, "status", item_id) == 0
    assert title in capsys.readouterr().out
    assert run(here, "list", "items", "--json") == 0
    rows = {r["item"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows[item_id]["title"] == title


def test_show_says_which_copy_counts(project, capsys):
    capsys.readouterr()
    assert run(project, "show", "p-aaaaaa") == 0
    out = capsys.readouterr().out
    assert str(wtf(project) / "foo" / "planning" / "draft" / "assigned.toml") in out and "bar" in out
    assert run(project, "show", "p-cccccc") == 0
    assert "on its branch only" in capsys.readouterr().out
    assert run(project, "show", "p-bbbbbb") == 0
    assert "the main checkout" in capsys.readouterr().out


def test_unassigning_reads_the_main_checkouts_copy_again(project, capsys):
    assert run(project, "assign", "p-aaaaaa", "--none") == 0
    capsys.readouterr()
    run(wtf(project) / "foo", "show", "p-aaaaaa", "--json")
    assert json.loads(capsys.readouterr().out)["data"]["title"] == "On main"


def test_assigned_to_a_worktree_without_a_copy_reads_the_main_checkouts(project, capsys):
    run(project, "assign", "p-bbbbbb", "bar")
    (wtf(project) / "bar" / "planning" / "draft" / "unassigned.toml").unlink()
    capsys.readouterr()
    assert run(project, "status", "p-bbbbbb") == 0
    out = capsys.readouterr().out
    assert "Stays on main" in out and "has no copy" in out


def test_a_gone_worktree_reads_the_main_checkouts_copy(project, capsys):
    import shutil
    shutil.rmtree(wtf(project) / "foo")
    git(project, "worktree", "prune")
    capsys.readouterr()
    assert run(project, "show", "p-aaaaaa", "--json") == 0
    assert json.loads(capsys.readouterr().out)["data"]["title"] == "On main"


@pytest.mark.parametrize("where", [0, 1, 2])
def test_an_approval_in_the_home_opens_the_gate_everywhere(project, capsys, where):
    from waterrise.state import step_hash
    f = folder.read(project / "planning", project)
    it = f.get("p-aaaaaa")
    step = it.steps()[0]
    answers.update(wtf(project) / "foo" / "planning" / "answers" / "p-aaaaaa.json", "p-aaaaaa",
                   lambda d: d["approvals"].update(s1={"verdict": "approved", "note": "", "at": "t",
                                                       "sha256": step_hash(step), "snapshot": step}))
    assert run(places(project)[where], "gate", "p-aaaaaa", "s1") == 0
    assert not (project / "planning" / "answers" / "p-aaaaaa.json").exists()


def test_a_move_happens_in_the_home(project, capsys):
    foo = wtf(project) / "foo"
    git(foo, "add", "planning")
    git(foo, "commit", "-qm", "the plan on its branch")
    f = folder.read(project / "planning", project)
    assert f.move_target(f.get("p-aaaaaa"), "todo") == foo / "planning" / "todo" / "assigned.toml"
    f.move(f.get("p-aaaaaa"), "todo")
    assert (foo / "planning" / "todo" / "assigned.toml").exists()
    assert (project / "planning" / "draft" / "assigned.toml").exists()
    assert "assigned.toml" in git(foo, "diff", "--cached", "--name-only")


def test_check_covers_the_copy_that_counts(project, capsys):
    capsys.readouterr()
    assert run(project, "check") == 0
    (wtf(project) / "foo" / "planning" / "draft" / "assigned.toml").write_text("not = toml = at all", encoding="utf-8")
    assert run(project, "check") == 1
    assert "not valid TOML" in capsys.readouterr().out
    assert run(project, "gate", "p-aaaaaa", "s1") == 1      # never answered for by the older copy


def test_a_new_item_lives_where_it_is_made(project, capsys):
    foo = wtf(project) / "foo"
    assert run(foo, "new", "plan", "made-here") == 0
    assert (foo / "planning" / "draft" / "made-here.toml").exists()
    assert not (project / "planning" / "draft" / "made-here.toml").exists()
    assert run(project, "new", "plan", "made-there") == 0
    assert (project / "planning" / "draft" / "made-there.toml").exists()
    capsys.readouterr()
    run(project, "list", "items", "--worktree", "foo", "--json")
    assert {r["title"] for r in json.loads(capsys.readouterr().out)} >= {"Made here", "Only on foo"}


def test_a_merge_brings_a_plan_back_to_the_main_checkout(project, capsys):
    foo = wtf(project) / "foo"
    git(foo, "add", "planning")
    git(foo, "commit", "-qm", "plans on the branch")
    git(project, "add", ".")
    git(project, "commit", "-qm", "assigned", "--allow-empty")
    git(project, "merge", "-q", "--no-edit", "foo")
    f = folder.read(project / "planning", project)
    assert "p-cccccc" not in f.homes
    assert f.get("p-cccccc").path == project / "planning" / "draft" / "only-here.toml"


def test_a_central_project_reads_its_central_folder_only(project, capsys):
    planning = central.planning_for("demo")
    planning.mkdir(parents=True)
    (planning / "planning.toml").write_text("format = 1\n", encoding="utf-8")
    entry = {"slug": "demo", "name": "demo", "path": str(project), "planning": str(planning), "home": "central"}
    path = client.state_dir() / client.REGISTRY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"repos": [entry]}), encoding="utf-8")
    capsys.readouterr()
    assert run(wtf(project) / "foo", "show", "p-aaaaaa") == 1
    assert run(project, "list", "items", "--json") == 0
    assert json.loads(capsys.readouterr().out) == []


def test_assigning_says_when_another_copy_starts_to_count(project, capsys):
    capsys.readouterr()
    assert run(project, "assign", "p-aaaaaa", "--none") == 0
    said = capsys.readouterr()
    assert "differs from the copy read until now" in said.out + said.err
    assert run(project, "assign", "p-bbbbbb", "bar") == 0       # the same in both: nothing to say
    out = capsys.readouterr()
    assert "differs" not in out.out + out.err


def test_a_new_worktree_is_not_sparse(project):
    assert (wtf(project) / "bar" / "planning" / "draft" / "assigned.toml").exists()
    assert git(wtf(project) / "bar", "status", "--porcelain") == ""
    assert git(project, "config", "--get-all", "extensions.worktreeConfig", check=False) == ""


def test_editing_a_plan_in_its_home_is_normal_and_a_copy_is_named(project, capsys):
    foo, bar = wtf(project) / "foo", wtf(project) / "bar"
    capsys.readouterr()
    assert run(foo, "check") == 0                # both of foo's edits are in their home
    assert "copy of" not in capsys.readouterr().out
    write_plan(bar, "p-aaaaaa", "assigned", "Edited in a copy")
    assert run(bar, "check") == 0
    out = capsys.readouterr().out
    assert "copy of p-aaaaaa" in out and str(foo / "planning" / "draft" / "assigned.toml") in out
    write_plan(foo, "p-bbbbbb", "unassigned", "Edited in foo")
    assert run(foo, "check") == 0
    out = capsys.readouterr().out
    assert "copy of p-bbbbbb" in out and str(project / "planning" / "draft" / "unassigned.toml") in out


def test_a_protocol_stays_in_the_main_checkout_and_survives_removing_the_worktree(project, capsys):
    foo = wtf(project) / "foo"
    assert run(foo, "note", "p-aaaaaa", "s1", "from the worktree") == 0
    protocol = project / "planning" / "protocols" / "p-aaaaaa" / "s1.md"
    assert "from the worktree" in protocol.read_text(encoding="utf-8")
    assert not (foo / "planning" / "protocols").exists()
    git(foo, "add", "planning")
    git(foo, "commit", "-qm", "plans on the branch")
    run(project, "assign", "p-aaaaaa", "--none")
    git(project, "merge", "-q", "--no-edit", "foo")
    assert run(project, "worktree", "remove", "foo") == 0
    assert protocol.exists()


def test_a_plan_only_on_an_unmerged_branch_holds_the_removal_back(project, capsys):
    foo = wtf(project) / "foo"
    run(project, "assign", "p-aaaaaa", "--none")
    git(foo, "add", "planning")
    git(foo, "commit", "-qm", "plans on the branch")
    capsys.readouterr()
    assert run(project, "worktree", "remove", "foo") == 1
    said = capsys.readouterr()
    assert "p-cccccc" in said.out + said.err and foo.exists()
    git(project, "merge", "-q", "--no-edit", "foo")
    assert run(project, "worktree", "remove", "foo") == 0
    assert not foo.exists()
    assert "foo" in git(project, "branch", "--list", "foo")


def test_the_mcp_tools_find_a_plan_in_its_home(project, monkeypatch):
    from waterrise import mcp as workplan
    monkeypatch.setattr(workplan, "command", lambda: [sys.executable, "-m", "waterrise"])
    monkeypatch.setattr(workplan, "page_url", lambda plan: None)
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
    foo = wtf(project) / "foo"
    for item_id, home in (("p-bbbbbb", None), ("p-aaaaaa", foo), ("p-cccccc", foo)):
        plan = workplan.resolve(item_id, foo)
        assert plan.repo == project
        out = workplan.tool_status(item_id, foo)
        if home is None:
            assert "git -C" not in out and "file: planning/draft/" in out
        else:
            assert f"git -C {home} commit" in out and str(home / "planning" / "draft") in out


@pytest.fixture
def service_on(project):
    import threading

    from waterrise import service
    srv = service.make_server(0, client.state_dir())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    status, entry = client.call(port, "POST", "/api/repos",
                                {"path": str(project), "planning": str(project / "planning"), "name": "repo"}, auth=True)
    assert status == 200
    yield project, port, entry["slug"]
    srv.shutdown()
    srv.server_close()


def test_the_page_shows_one_card_per_plan_with_its_branch(service_on):
    project, port, slug = service_on
    _, view = client.call(port, "GET", f"/api/r/{slug}")
    ids = [i["id"] for i in view["items"]]
    assert sorted(ids) == ["p-aaaaaa", "p-bbbbbb", "p-cccccc"]
    items = {i["id"]: i for i in view["items"]}
    assert items["p-aaaaaa"]["title"] == "On the branch" and items["p-aaaaaa"]["home"]["branch"] == "foo"
    assert items["p-cccccc"]["home"]["name"] == "foo" and items["p-cccccc"]["worktree"] is None
    assert items["p-bbbbbb"]["home"] is None
    trees = {w["name"]: w for w in view["worktrees"]}
    assert set(trees["foo"]["plans"]) == {"p-aaaaaa", "p-cccccc"}


def test_answers_on_the_page_are_written_in_the_plans_home(service_on):
    project, port, slug = service_on
    foo = wtf(project) / "foo"
    _, item = client.call(port, "GET", f"/api/r/{slug}/item/p-aaaaaa")
    assert item["home"]["name"] == "foo"
    status, _ = client.call(port, "PUT", f"/api/r/{slug}/approval/p-aaaaaa/s1",
                            {"verdict": "approved", "sha256": item["approvals"]["s1"]["current"]}, auth=True)
    assert status == 200
    status, _ = client.call(port, "POST", f"/api/r/{slug}/comment/p-aaaaaa", {"on": "", "text": "hello"}, auth=True)
    assert status == 200
    written = json.loads((foo / "planning" / "answers" / "p-aaaaaa.json").read_text(encoding="utf-8"))
    assert written["approvals"]["s1"]["verdict"] == "approved" and written["comments"][0]["text"] == "hello"
    assert not (project / "planning" / "answers" / "p-aaaaaa.json").exists()
    for here in places(project):
        assert run(here, "gate", "p-aaaaaa", "s1") == 0


def test_a_move_on_the_page_runs_in_the_home(service_on):
    project, port, slug = service_on
    foo = wtf(project) / "foo"
    git(foo, "add", "planning")
    git(foo, "commit", "-qm", "plans on the branch")
    _, item = client.call(port, "GET", f"/api/r/{slug}/item/p-cccccc")
    status, _ = client.call(port, "PUT", f"/api/r/{slug}/approval/p-cccccc",
                            {"verdict": "approved", "sha256": item["plan_approval"]["current"]}, auth=True)
    assert status == 200
    status, body = client.call(port, "PUT", f"/api/r/{slug}/move/p-cccccc", {"folder": "todo"}, auth=True)
    assert status == 200, body
    assert body["file"] == "todo/only-here.toml"
    assert "only-here.toml" in git(foo, "diff", "--cached", "--name-only")


def test_the_page_is_told_when_another_copy_starts_to_count(service_on):
    project, port, slug = service_on
    status, body = client.call(port, "PUT", f"/api/r/{slug}/worktree/p-aaaaaa", {"name": None}, auth=True)
    assert status == 200 and "differs" in body["warning"]


def test_the_service_watches_the_worktrees_planning_folders(service_on):
    from waterrise import service
    project, port, slug = service_on
    entry = {"slug": slug, "path": str(project), "planning": str(project / "planning")}
    before = service._signature(entry)
    write_plan(wtf(project) / "bar", "p-dddddd", "new", "Made in bar")
    assert service._signature(entry) != before


# ---- identical copies are parsed once (p-pw7ft8 s3-6) ------------------------------ #

def test_an_edited_copy_is_read_with_its_own_text(project):
    f = folder.read(project / "planning", project)
    main = f.main_items["p-bbbbbb"]
    bar = f.copies["bar"].get("p-bbbbbb")
    assert bar.data is main.data, "an identical copy shares what was parsed"
    copy = wtf(project) / "bar" / "planning" / "draft" / "unassigned.toml"
    copy.write_text(copy.read_text(encoding="utf-8").replace("Stays on main", "Edited in bar"), encoding="utf-8")
    f = folder.read(project / "planning", project)
    assert f.copies["bar"].get("p-bbbbbb").title == "Edited in bar"
    assert f.main_items["p-bbbbbb"].title == "Stays on main"
    assert f.get("p-bbbbbb").title == "Stays on main", "the main checkout's copy still counts"


def test_a_broken_copy_in_its_home_has_its_own_problem(project):
    home = wtf(project) / "foo" / "planning" / "draft" / "assigned.toml"
    home.write_text(home.read_text(encoding="utf-8") + "\nthis is not toml\n", encoding="utf-8")
    f = folder.read(project / "planning", project)
    broken = [it for it in f.items if it.path == home]
    assert broken and not broken[0].parsed
    assert any("not valid TOML" in p.message for p in broken[0].problems)
    assert f.main_items["p-aaaaaa"].parsed and not f.main_items["p-aaaaaa"].problems


def test_the_cache_stays_right_when_it_evicts(project, monkeypatch):
    monkeypatch.setattr(folder, "_PARSED_LIMIT", 1)      # every new text evicts the one before
    monkeypatch.setattr(folder, "_PARSED", {})
    monkeypatch.setattr(folder, "_parsed_size", 0)
    f = folder.read(project / "planning", project)
    assert {it.id: it.title for it in f.items if it.id} == {
        "p-aaaaaa": "On the branch", "p-bbbbbb": "Stays on main", "p-cccccc": "Only on foo"}
    assert len(folder._PARSED) <= 1
