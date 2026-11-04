"""All worktrees of a repository are one project."""

import json
import subprocess
from pathlib import Path

import pytest

from tests.test_planning import run
from waterrise import answers, client, context, service
from waterrise import mcp as workplan


def git(root: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.org", *args],
                          capture_output=True, text=True, check=check, encoding="utf-8").stdout


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    for name in ("WATERRISE_PROJECT", "WATERRISE_PLANNING_DIR", "WATERRISE_PROJECT_NAME", "WATERRISE_PROG"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


@pytest.fixture
def proj(env):
    """A repository with a commit and a planning folder, and one linked worktree
    in `worktrees/foo`."""
    root = (env / "proj").resolve()
    (root / "planning" / "todo").mkdir(parents=True)
    (root / "planning" / "planning.toml").write_text('format = 1\nuser = "u"\n', encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "file").write_text("x\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-qm", "first")
    git(root, "worktree", "add", "-q", "worktrees/foo", "-b", "foo")
    return root


def register(*entries: dict) -> None:
    path = client.state_dir() / client.REGISTRY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"repos": list(entries)}), encoding="utf-8")


def entry(path: Path, **more) -> dict:
    return {"path": str(path), "planning": str(path / "planning"), "name": path.name, "slug": path.name, **more}


def test_in_a_worktree_with_the_main_checkout_registered(proj):
    register(entry(proj))
    wt = proj / "worktrees" / "foo"
    ctx = context.resolve(wt)
    assert (ctx.root, ctx.planning, ctx.slug) == (proj, proj / "planning", "proj")
    assert ctx.checkout == wt


def test_in_a_worktree_through_butler(proj, monkeypatch):
    register(entry(proj))
    wt = proj / "worktrees" / "foo"
    monkeypatch.setenv(context.PROJECT_ENV, str(wt))
    monkeypatch.setenv(context.PLANNING_ENV, "planning")
    monkeypatch.setenv(context.NAME_ENV, "demo")
    ctx = context.resolve(wt)
    assert (ctx.root, ctx.planning, ctx.slug, ctx.name) == (proj, proj / "planning", "proj", "demo")
    assert ctx.checkout == wt


def test_in_a_worktree_with_nothing_registered(proj):
    wt = proj / "worktrees" / "foo"
    ctx = context.resolve(wt / "sub")
    assert (ctx.root, ctx.planning, ctx.slug) == (proj, proj / "planning", None)
    assert ctx.checkout == wt


def test_in_a_worktree_beside_the_checkout(proj, env):
    sib = env / "sib"
    git(proj, "worktree", "add", "-q", str(sib), "-b", "sib")
    for registered in ((), (entry(proj),)):
        register(*registered)
        ctx = context.resolve(sib)
        assert (ctx.root, ctx.planning) == (proj, proj / "planning")
        assert ctx.checkout == sib.resolve()


def test_a_registered_project_inside_the_repository_is_found_from_a_worktree(proj):
    (proj / "sub" / "planning").mkdir()
    register(entry(proj), entry(proj / "sub"))
    wt = proj / "worktrees" / "foo"
    ctx = context.resolve(wt / "sub")
    assert (ctx.root, ctx.slug) == (proj / "sub", "sub")
    assert ctx.checkout == wt


def test_a_folder_only_on_the_worktrees_branch_still_resolves(proj):
    wt = proj / "worktrees" / "foo"
    (wt / "new" / "deep").mkdir(parents=True)
    assert context.resolve(wt / "new" / "deep").root == proj


def test_the_main_checkout_is_its_own_checkout(proj):
    ctx = context.resolve(proj / "sub")
    assert (ctx.root, ctx.checkout) == (proj, proj)
    assert not context.locate(proj).linked
    assert context.locate(proj / "worktrees" / "foo").linked


def test_a_clone_stays_a_separate_project(proj, env):
    clone = env / "clone"
    git(env, "clone", "-q", str(proj), str(clone))
    register(entry(proj))
    ctx = context.resolve(clone)
    assert (ctx.root, ctx.planning, ctx.slug) == (clone.resolve(), clone.resolve() / "planning", None)


def test_a_bare_repository_keeps_todays_behaviour(proj, env):
    bare = env / "bare.git"
    git(env, "clone", "-q", "--bare", str(proj), str(bare))
    wt = env / "bare-wt"
    git(bare, "worktree", "add", "-q", str(wt), "-b", "side")
    ctx = context.resolve(wt)
    assert (ctx.root, ctx.planning, ctx.checkout) == (wt.resolve(), wt.resolve() / "planning", wt.resolve())
    assert context.resolve(bare).root == bare.resolve()


def test_a_folder_outside_git_is_its_own_project(env):
    plain = (env / "plain").resolve()
    plain.mkdir()
    ctx = context.resolve(plain)
    assert (ctx.root, ctx.checkout) == (plain, plain)


def test_a_central_project_is_found_from_a_worktree(proj, env):
    home = env / "data" / "waterrise" / "plans" / "proj" / "planning"
    register(entry(proj) | {"home": "central", "planning": str(home)})
    wt = proj / "worktrees" / "foo"
    ctx = context.resolve(wt)
    assert (ctx.root, ctx.planning, ctx.home, ctx.checkout) == (proj, home, "central", wt)


def test_the_mcp_server_counts_a_worktree_as_its_project(proj):
    wt = proj / "worktrees" / "foo"
    (proj / "planning" / "todo" / "a.toml").write_text('id = "p-aaaaaa"\n', encoding="utf-8")
    (wt / "planning" / "todo").mkdir(parents=True, exist_ok=True)
    stale = wt / "planning" / "todo" / "a.toml"
    stale.write_text('id = "p-aaaaaa"\n', encoding="utf-8")
    assert workplan.known_repos(wt)[0][1] == proj
    assert workplan.resolve("p-aaaaaa", wt).repo == proj
    assert workplan.resolve(str(stale), wt).repo == proj
    assert workplan.resolve(f"{wt}:p-aaaaaa", wt).repo == proj


def registry_at(state: Path, *entries: dict) -> service.Registry:
    path = state / "registry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"repos": list(entries)}), encoding="utf-8")
    return service.Registry(path)


def test_registering_from_a_worktree_joins_the_main_entry(proj, env):
    reg = registry_at(env / "s")
    wt = proj / "worktrees" / "foo"
    first = reg.register(str(wt), str(wt / "planning"), "proj")
    assert (first["path"], first["planning"]) == (str(proj), str(proj / "planning"))
    assert reg.register(str(proj), str(proj / "planning"), "proj")["slug"] == first["slug"]
    assert reg.register(str(wt), str(wt / "planning"), "proj")["slug"] == first["slug"]
    assert len(reg.repos) == 1


def test_an_old_worktree_entry_goes_at_start_if_its_main_is_registered(proj, env, capsys):
    wt = proj / "worktrees" / "foo"
    (env / "s").mkdir()
    (env / "s" / "registry.json").write_text(json.dumps({"repos": [
        entry(proj), entry(wt) | {"slug": "proj-foo"}]}), encoding="utf-8")
    reg = service._registry(env / "s")
    assert [r["slug"] for r in reg.repos] == ["proj"]
    assert "dropped proj-foo" in capsys.readouterr().err
    assert (wt / "sub" / "file").exists()
    saved = json.loads((env / "s" / "registry.json").read_text(encoding="utf-8"))
    assert [r["slug"] for r in saved["repos"]] == ["proj"]


def test_an_old_worktree_entry_becomes_the_main_entry_if_that_is_missing(proj, env):
    wt = proj / "worktrees" / "foo"
    sib = env / "sib"
    git(proj, "worktree", "add", "-q", str(sib), "-b", "sib")
    reg = registry_at(env / "s", entry(wt) | {"slug": "foo"}, entry(sib.resolve()) | {"slug": "sib"})
    said = reg.collapse_worktrees()
    assert [(r["slug"], r["path"], r["planning"]) for r in reg.repos] == [("foo", str(proj), str(proj / "planning"))]
    assert len(said) == 2


def test_a_worktrees_central_entry_is_kept_where_its_plans_are(proj, env):
    wt = proj / "worktrees" / "foo"
    central = {"home": "central", "planning": str(env / "c" / "planning"), "slug": "foo"}
    reg = registry_at(env / "s", entry(proj), entry(wt) | central)
    assert "plans in its own central home" in reg.collapse_worktrees()[0]
    assert [r["slug"] for r in reg.repos] == ["proj", "foo"]


def test_a_clone_and_a_missing_folder_keep_their_entries(proj, env):
    clone = env / "clone"
    git(env, "clone", "-q", str(proj), str(clone))
    reg = registry_at(env / "s", entry(proj), entry(clone.resolve()), entry(env / "gone"))
    assert reg.collapse_worktrees() == []
    assert len(reg.repos) == 3


def edit_copy(wt: Path) -> None:
    with (wt / "planning" / "planning.toml").open("a", encoding="utf-8") as f:
        f.write("# edited\n")


@pytest.mark.parametrize("where", ["worktrees/foo", "../sib"])
def test_check_in_a_worktree_names_an_edited_copy_and_the_right_folder(proj, where, capsys):
    wt = (proj / where).resolve()
    if not wt.exists():
        git(proj, "worktree", "add", "-q", str(wt), "-b", "sib")
    assert run(wt, "check") == 0
    assert "edited in this worktree" not in capsys.readouterr().out
    edit_copy(wt)
    assert run(wt, "check") == 0
    out = capsys.readouterr().out
    assert "planning.toml" in out and str(proj / "planning") in out and "edit it there" in out
    assert run(wt, "check", "--strict") == 1


def test_check_names_what_commits_on_the_branch_changed(proj, capsys):
    wt = proj / "worktrees" / "foo"
    edit_copy(wt)
    git(wt, "commit", "-qam", "in the copy")
    capsys.readouterr()
    assert run(wt, "check") == 0
    assert "edit it there" in capsys.readouterr().out


def test_check_elsewhere_says_nothing_about_other_worktrees(proj, capsys):
    edit_copy(proj / "worktrees" / "foo")
    assert run(proj, "check") == 0
    assert "edit it there" not in capsys.readouterr().out


def test_the_main_checkout_moving_on_is_not_an_edit(proj, capsys):
    (proj / "planning" / "todo" / "note.txt").write_text("x\n", encoding="utf-8")
    git(proj, "add", ".")
    git(proj, "commit", "-qm", "main moves on")
    capsys.readouterr()
    run(proj / "worktrees" / "foo", "check")
    assert "edit it there" not in capsys.readouterr().out


# ---- worktree add, list and remove ------------------------------------------------ #

@pytest.fixture
def repo(env):
    """A repository whose planning folder is tracked, and no worktree yet."""
    root = (env / "repo").resolve()
    root.mkdir()
    git(root, "init", "-q")
    assert run(root, "init") == 0
    (root / "code").write_text("x\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "first")
    return root


def status(root: Path) -> str:
    return git(root, "status", "--porcelain")


def test_add_makes_a_clean_worktree_with_planning(repo):
    assert run(repo, "worktree", "add", "foo") == 0
    wt = repo / "worktrees" / "foo"
    assert (wt / "code").exists() and (wt / "planning" / "planning.toml").exists()
    assert status(wt) == "" and status(repo) == ""
    assert git(wt, "branch", "--show-current").strip() == "foo"


def test_a_commit_in_a_new_worktree_merges_with_every_planning_file_kept(repo):
    run(repo, "worktree", "add", "foo")
    wt = repo / "worktrees" / "foo"
    (wt / "more").write_text("y\n", encoding="utf-8")
    git(wt, "add", "more")
    git(wt, "commit", "-qm", "more")
    git(repo, "merge", "-q", "foo")
    assert (repo / "more").exists() and (repo / "planning" / "planning.toml").exists()


def test_add_from_inside_another_worktree_lands_in_the_main_checkout(repo):
    run(repo, "worktree", "add", "foo")
    assert run(repo / "worktrees" / "foo", "worktree", "add", "bar") == 0
    assert (repo / "worktrees" / "bar" / "code").exists()
    assert not (repo / "worktrees" / "foo" / "worktrees").exists()


def test_add_uses_an_existing_branch_and_from(repo):
    first = git(repo, "rev-parse", "HEAD").strip()
    (repo / "later").write_text("z\n", encoding="utf-8")
    git(repo, "add", "later")
    git(repo, "commit", "-qm", "later")
    git(repo, "branch", "side")
    assert run(repo, "worktree", "add", "a", "--branch", "side") == 0
    assert git(repo / "worktrees" / "a", "branch", "--show-current").strip() == "side"
    assert run(repo, "worktree", "add", "b", "--from", first) == 0
    assert git(repo / "worktrees" / "b", "rev-parse", "HEAD").strip() == first
    assert run(repo, "worktree", "add", "c", "--branch", "side", "--from", first) == 1


@pytest.mark.parametrize("name", ["main", "a/b", "a\\b", "..", "."])
def test_add_refuses_a_bad_name(repo, name):
    assert run(repo, "worktree", "add", name) == 1
    assert not (repo / "worktrees").exists() or not any((repo / "worktrees").iterdir())


def test_add_refuses_a_folder_that_exists(repo):
    (repo / "worktrees" / "foo").mkdir(parents=True)
    assert run(repo, "worktree", "add", "foo") == 1


def test_list_shows_the_convention_and_all_adds_the_others(repo, env, capsys):
    run(repo, "worktree", "add", "foo")
    git(repo, "worktree", "add", "-q", str(env / "elsewhere"), "-b", "elsewhere")
    (repo / "worktrees" / "foo" / "dirt").write_text("d\n", encoding="utf-8")
    capsys.readouterr()
    assert run(repo, "worktree", "list", "--json") == 0
    rows = json.loads(capsys.readouterr().out)
    assert [(r["name"], r["clean"], r["here"]) for r in rows] == [("main", True, True), ("foo", False, False)]
    assert run(repo, "worktree", "list", "--all", "--json") == 0
    rows = json.loads(capsys.readouterr().out)
    assert {r["name"]: r["assignable"] for r in rows} == {"main": True, "foo": True, "elsewhere": False}
    assert run(repo, "worktree", "list", "--all") == 0
    assert "not assignable" in capsys.readouterr().out


def test_list_counts_ahead_and_behind(repo, capsys):
    run(repo, "worktree", "add", "foo")
    wt = repo / "worktrees" / "foo"
    (wt / "a").write_text("a\n", encoding="utf-8")
    git(wt, "add", "a")
    git(wt, "commit", "-qm", "a")
    capsys.readouterr()
    run(repo, "worktree", "list", "--json")
    foo = json.loads(capsys.readouterr().out)[1]
    assert (foo["ahead"], foo["behind"]) == (1, 0)


def test_remove_keeps_the_branch_and_refuses_what_git_refuses(repo):
    run(repo, "worktree", "add", "foo")
    wt = repo / "worktrees" / "foo"
    (wt / "dirt").write_text("d\n", encoding="utf-8")
    assert run(repo, "worktree", "remove", "foo") == 1
    assert wt.exists()
    (wt / "dirt").unlink()
    assert run(repo, "worktree", "remove", "foo") == 0
    assert not wt.exists()
    assert "foo" in git(repo, "branch", "--list", "foo")
    assert run(repo, "worktree", "remove", "foo") == 1
    assert run(repo, "worktree", "remove", "main") == 1


def test_remove_is_held_back_by_an_open_assigned_plan(repo, capsys):
    run(repo, "worktree", "add", "foo")
    run(repo, "new", "plan", "work", "--folder", "todo")
    capsys.readouterr()
    run(repo, "list", "items", "--json")
    plan = json.loads(capsys.readouterr().out)[0]["item"]
    run(repo, "assign", plan, "foo")
    capsys.readouterr()
    assert run(repo, "worktree", "remove", "foo") == 1
    assert plan in capsys.readouterr().err
    assert (repo / "worktrees" / "foo").exists()


# ---- the ignore rule ------------------------------------------------------------- #

def test_a_fresh_init_ignores_the_folder(repo):
    assert "/worktrees/" in (repo / ".gitignore").read_text(encoding="utf-8")
    git(repo, "check-ignore", "worktrees/x")


def test_add_writes_the_rule_in_an_existing_gitignore_and_leaves_it_uncommitted(repo, capsys):
    (repo / ".gitignore").write_text("*.log", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "another rule")
    capsys.readouterr()
    assert run(repo, "worktree", "add", "foo") == 0
    assert (repo / ".gitignore").read_text(encoding="utf-8") == "*.log\n/worktrees/\n"
    assert "not committed" in capsys.readouterr().out
    assert status(repo) == " M .gitignore\n"


def test_a_broader_rule_is_enough(repo):
    (repo / ".gitignore").write_text("worktrees\n", encoding="utf-8")
    assert run(repo, "worktree", "add", "foo") == 0
    assert (repo / ".gitignore").read_text(encoding="utf-8") == "worktrees\n"


def test_check_warns_only_when_the_folder_exists_and_is_not_ignored(repo, capsys):
    (repo / ".gitignore").unlink()
    capsys.readouterr()
    run(repo, "check")
    assert "is not ignored" not in capsys.readouterr().out
    (repo / "worktrees").mkdir()
    run(repo, "check")
    assert "is not ignored" in capsys.readouterr().out
    (repo / ".gitignore").write_text("/worktrees/\n", encoding="utf-8")
    run(repo, "check")
    assert "is not ignored" not in capsys.readouterr().out


def test_a_central_project_uses_the_exclude_file(env, capsys):
    root = (env / "c").resolve()
    root.mkdir()
    git(root, "init", "-q")
    (root / "code").write_text("x\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "first")
    from waterrise import worktrees
    ctx = context.Project(root=root, planning=env / "elsewhere" / "planning", name="c", home="central", checkout=root)
    path = worktrees.ensure_ignored(ctx)
    assert path == root / ".git" / "info" / "exclude"
    assert status(root) == "" and not (root / ".gitignore").exists()
    git(root, "check-ignore", "worktrees/x")


# ---- assigning ------------------------------------------------------------------- #

@pytest.fixture
def planned(repo, capsys):
    """`repo` with a worktree foo, a worktree bar, and a plan in todo/."""
    run(repo, "worktree", "add", "foo")
    run(repo, "worktree", "add", "bar")
    run(repo, "new", "plan", "work", "--folder", "todo")
    capsys.readouterr()
    run(repo, "list", "items", "--json")
    plan = json.loads(capsys.readouterr().out)[0]["item"]
    return repo, plan


def stored(repo: Path, plan: str) -> dict:
    """What the assignments file says about the plan, under the key it is read by."""
    plans, _ = answers.read_assignments(repo / "planning" / "assignments.json")
    return {"worktree": plans[plan]} if plan in plans else {}


def test_assign_reassign_and_unassign(planned):
    repo, plan = planned
    assert run(repo, "assign", plan, "foo") == 0
    entry = stored(repo, plan)["worktree"]
    assert entry["name"] == "foo" and entry["by"] == "command" and entry["at"]
    assert run(repo / "worktrees" / "bar", "assign", plan, "bar") == 0
    assert stored(repo, plan)["worktree"]["name"] == "bar"
    assert run(repo, "assign", plan, "main") == 0
    assert stored(repo, plan)["worktree"]["name"] == "main"
    assert run(repo, "assign", plan, "--none") == 0
    assert "worktree" not in stored(repo, plan)


def test_assign_refuses_what_cannot_be_assigned(planned, env, capsys):
    repo, plan = planned
    git(repo, "worktree", "add", "-q", str(env / "elsewhere"), "-b", "elsewhere")
    capsys.readouterr()
    assert run(repo, "assign", plan, "nope") == 1
    assert "foo" in capsys.readouterr().err
    assert run(repo, "assign", plan, "elsewhere") == 1
    assert "outside" in capsys.readouterr().err
    assert run(repo, "assign", plan) == 1
    assert run(repo, "assign", plan, "foo", "--none") == 1
    assert "worktree" not in stored(repo, plan)
    run(repo, "new", "note", "context")
    capsys.readouterr()
    run(repo, "list", "items", "--json")
    note = next(r["item"] for r in json.loads(capsys.readouterr().out) if r["kind"] == "note")
    assert run(repo, "assign", note, "foo") == 1


def test_add_with_a_plan_assigns_it(planned):
    repo, plan = planned
    assert run(repo, "worktree", "add", "baz", "--plan", plan) == 0
    assert stored(repo, plan)["worktree"]["name"] == "baz"
    assert run(repo, "worktree", "add", "qux", "--plan", "p-zzzzzz") != 0
    assert not (repo / "worktrees" / "qux").exists()


def test_an_assignment_does_not_touch_an_approval(planned):
    repo, plan = planned
    before = stored(repo, plan).get("plan_approval")
    run(repo, "assign", plan, "foo")
    assert stored(repo, plan).get("plan_approval") == before


# ---- showing it ------------------------------------------------------------------ #

def test_status_show_and_list_say_where_a_plan_belongs(planned, capsys):
    repo, plan = planned
    run(repo, "assign", plan, "foo")
    foo = str(repo / "worktrees" / "foo")
    capsys.readouterr()
    run(repo, "status", plan)
    assert f"worktree: foo  {foo}" in capsys.readouterr().out
    run(repo, "show", plan)
    assert f"worktree: foo  {foo}" in capsys.readouterr().out
    run(repo, "status", plan, "--json")
    assert json.loads(capsys.readouterr().out)[0]["worktree"] == {"name": "foo", "path": foo, "gone": False}
    run(repo, "show", plan, "--json")
    assert json.loads(capsys.readouterr().out)["worktree"]["path"] == foo
    run(repo, "list")
    assert "[foo]" in capsys.readouterr().out
    run(repo, "list", "items", "--worktree", "foo", "--json")
    assert [r["item"] for r in json.loads(capsys.readouterr().out)] == [plan]
    run(repo, "list", "items", "--worktree", "bar", "--json")
    assert json.loads(capsys.readouterr().out) == []
    run(repo, "worktree", "list", "--json")
    rows = {r["name"]: r["plans"] for r in json.loads(capsys.readouterr().out)}
    assert rows == {"main": [], "foo": [plan], "bar": []}


def test_status_in_a_worktree_says_where_and_puts_its_plans_first(planned, capsys):
    repo, plan = planned
    run(repo, "new", "plan", "other", "--folder", "todo")
    capsys.readouterr()
    run(repo, "assign", plan, "bar")
    capsys.readouterr()
    run(repo, "list", "items", "--json")
    ids = [r["item"] for r in json.loads(capsys.readouterr().out)]
    other = next(i for i in ids if i != plan)
    run(repo, "assign", other, "foo")
    capsys.readouterr()
    run(repo / "worktrees" / "foo", "status")
    out = capsys.readouterr().out
    assert out.startswith("in the worktree foo,") and str(repo) in out.splitlines()[0]
    assert out.index(other) < out.index(plan)
    run(repo, "status")
    assert not capsys.readouterr().out.startswith("in the worktree")


def test_a_gone_worktree_is_reported_never_silently_unassigned(planned, capsys):
    repo, plan = planned
    run(repo, "assign", plan, "foo")
    git(repo, "worktree", "remove", str(repo / "worktrees" / "foo"))
    capsys.readouterr()
    run(repo, "check")
    assert "no longer exists" in capsys.readouterr().out
    run(repo, "status", plan)
    assert "(gone)" in capsys.readouterr().out
    run(repo, "status", plan, "--json")
    assert json.loads(capsys.readouterr().out)[0]["worktree"]["gone"] is True
    assert stored(repo, plan)["worktree"]["name"] == "foo"


def test_a_finished_plan_keeps_its_record_quietly(planned, capsys):
    repo, plan = planned
    run(repo, "assign", plan, "foo")
    git(repo, "worktree", "remove", str(repo / "worktrees" / "foo"))
    path = next((repo / "planning" / "todo").glob("*.toml"))
    path.rename(repo / "planning" / "done" / path.name)
    capsys.readouterr()
    run(repo, "check")
    assert "no longer exists" not in capsys.readouterr().out


# ---- working from the wrong place ------------------------------------------------- #

@pytest.fixture
def working(repo, capsys):
    """A plan in todo/ with an agent step, in a repository with worktrees foo and bar."""
    from tests.test_planning import plan_text
    run(repo, "worktree", "add", "foo")
    run(repo, "worktree", "add", "bar")
    (repo / "planning" / "todo" / "m.toml").write_text(plan_text(), encoding="utf-8")
    capsys.readouterr()
    return repo, "p-4k7x2m", "s1-1"


def protocol_text(repo: Path, plan: str, step: str) -> str:
    path = repo / "planning" / "protocols" / plan / f"{step}.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


@pytest.mark.parametrize("command", ["note", "run"])
def test_the_wrong_place(working, command, capsys):
    repo, plan, step = working
    argv = (["note", plan, step, "hello"] if command == "note"
            else ["run", plan, step, "--", "git", "--version"])
    assert run(repo, "assign", plan, "foo") == 0
    capsys.readouterr()
    # another linked worktree: refused, with the right path
    assert run(repo / "worktrees" / "bar", *argv) == 1
    assert str(repo / "worktrees" / "foo") in capsys.readouterr().err
    assert protocol_text(repo, plan, step) == ""
    # the main checkout: a warning, and on
    assert run(repo, *argv) == 0
    captured = capsys.readouterr()
    assert "this is the main checkout" in captured.out + captured.err
    assert protocol_text(repo, plan, step) != ""
    # the assigned one: nothing said
    assert run(repo / "worktrees" / "foo", *argv) == 0
    captured = capsys.readouterr()
    assert "assigned to" not in captured.out + captured.err


def test_a_worktree_outside_the_folder_is_refused_too(working, env, capsys):
    repo, plan, step = working
    git(repo, "worktree", "add", "-q", str(env / "elsewhere"), "-b", "elsewhere")
    run(repo, "assign", plan, "foo")
    assert run(env / "elsewhere", "note", plan, step, "hello") == 1


def test_an_unassigned_plan_can_be_worked_on_anywhere(working, capsys):
    repo, plan, step = working
    assert run(repo / "worktrees" / "bar", "note", plan, step, "a") == 0
    assert run(repo, "note", plan, step, "b") == 0
    captured = capsys.readouterr()
    assert "assigned to" not in captured.out + captured.err


def test_a_plan_assigned_to_main_stops_a_worktree_and_not_the_main_checkout(working, capsys):
    repo, plan, step = working
    run(repo, "assign", plan, "main")
    capsys.readouterr()
    assert run(repo / "worktrees" / "foo", "note", plan, step, "a") == 1
    capsys.readouterr()
    assert run(repo, "note", plan, step, "b") == 0
    captured = capsys.readouterr()
    assert "assigned to" not in captured.out + captured.err


def test_a_gone_worktree_stops_nobody(working, capsys):
    repo, plan, step = working
    run(repo, "assign", plan, "foo")
    git(repo, "worktree", "remove", str(repo / "worktrees" / "foo"))
    capsys.readouterr()
    assert run(repo / "worktrees" / "bar", "note", plan, step, "a") == 0
    captured = capsys.readouterr()
    assert "no longer exists" in captured.out + captured.err


@pytest.mark.parametrize("assigned", [False, True])
def test_gates_exit_status_does_not_depend_on_where_it_runs(working, assigned, capsys):
    repo, plan, _ = working
    if assigned:
        run(repo, "assign", plan, "foo")
    for where in (repo, repo / "worktrees" / "foo", repo / "worktrees" / "bar"):
        capsys.readouterr()
        assert run(where, "gate", plan) == 1
        captured = capsys.readouterr()
        assert ("is assigned to foo" in captured.out + captured.err) is assigned
        assert "gate closed" in captured.out + captured.err


def test_reading_commands_never_refuse(working, capsys):
    repo, plan, _ = working
    run(repo, "assign", plan, "foo")
    bar = repo / "worktrees" / "bar"
    for argv in (["status", plan], ["show", plan], ["list"]):
        assert run(bar, *argv) == 0
    capsys.readouterr()
    run(bar, "check")       # exits 1 on this unapproved plan in todo/, and for no other reason
    captured = capsys.readouterr()
    assert "not been approved" in captured.out and "work there" not in captured.err


# ---- the page -------------------------------------------------------------------- #

@pytest.fixture
def page(planned, env):
    """The API's views and the route, on a real service for `planned`'s repository."""
    import threading
    repo, plan = planned
    srv = service.make_server(0, client.state_dir())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    status, entry = client.call(port, "POST", "/api/repos",
                                {"path": str(repo), "planning": str(repo / "planning"), "name": "repo"}, auth=True)
    assert status == 200
    yield repo, plan, port, entry["slug"]
    srv.shutdown()
    srv.server_close()


def test_the_repositorys_view_has_worktrees_and_assignments(page):
    repo, plan, port, slug = page
    run(repo, "assign", plan, "foo")
    (repo / "worktrees" / "foo" / "dirt").write_text("d\n", encoding="utf-8")
    _, view = client.call(port, "GET", f"/api/r/{slug}")
    trees = {w["name"]: w for w in view["worktrees"]}
    assert set(trees) == {"main", "foo", "bar"}
    assert trees["foo"]["clean"] is False and trees["bar"]["clean"] is True and trees["foo"]["plans"] == [plan]
    it = next(i for i in view["items"] if i["id"] == plan)
    assert it["worktree"] == {"name": "foo", "path": str(repo / "worktrees" / "foo"), "gone": False}


def test_the_index_counts_worktrees_without_asking_git(page, monkeypatch):
    repo, plan, port, slug = page
    def no_git(*a, **k):
        raise AssertionError("git in the index")
    monkeypatch.setattr(service.worktrees_mod, "read", no_git)
    view = service.repo_view({"slug": slug, "path": str(repo), "planning": str(repo / "planning")}, deep=False)
    assert view["worktree_count"] == 2 and "worktrees" not in view
    assert "worktree" not in next(i for i in view["items"] if i["id"] == plan)


def test_a_gone_worktree_shows_as_gone_in_the_views(page):
    repo, plan, port, slug = page
    run(repo, "assign", plan, "foo")
    git(repo, "worktree", "remove", str(repo / "worktrees" / "foo"))
    _, view = client.call(port, "GET", f"/api/r/{slug}")
    assert next(i for i in view["items"] if i["id"] == plan)["worktree"]["gone"] is True
    assert "foo" not in [w["name"] for w in view["worktrees"]]
    _, item = client.call(port, "GET", f"/api/r/{slug}/item/{plan}")
    assert item["worktree"]["gone"] is True and "foo" not in item["worktrees"]


def test_the_route_assigns_and_unassigns(page):
    repo, plan, port, slug = page
    status, body = client.call(port, "PUT", f"/api/r/{slug}/worktree/{plan}", {"name": "foo"}, auth=True)
    assert status == 200 and body["worktree"]["name"] == "foo"
    assert stored(repo, plan)["worktree"]["by"] == "page"
    _, item = client.call(port, "GET", f"/api/r/{slug}/item/{plan}")
    assert item["worktree"]["name"] == "foo" and set(item["worktrees"]) == {"main", "foo", "bar"}
    status, body = client.call(port, "PUT", f"/api/r/{slug}/worktree/{plan}", {"name": None}, auth=True)
    assert status == 200 and body["worktree"] is None
    assert "worktree" not in stored(repo, plan)


@pytest.mark.parametrize("name", ["nope", 7])
def test_the_route_refuses_a_name_and_changes_nothing(page, name):
    repo, plan, port, slug = page
    client.call(port, "PUT", f"/api/r/{slug}/worktree/{plan}", {"name": "foo"}, auth=True)
    status, _ = client.call(port, "PUT", f"/api/r/{slug}/worktree/{plan}", {"name": name}, auth=True)
    assert status in (400, 409)
    assert stored(repo, plan)["worktree"]["name"] == "foo"


def test_the_route_is_protected_like_the_other_answers(page):
    from tests.test_planning_service import req
    repo, plan, port, slug = page
    status = req(port, "PUT", f"/api/r/{slug}/worktree/{plan}", {"name": "foo"}, headers={})[0]
    assert status == 403
    assert "worktree" not in stored(repo, plan)


# ---- the assignment has a file of its own ----------------------------------------- #

def test_an_assignment_and_answers_on_the_branch_merge_without_a_conflict(repo, capsys):
    run(repo, "new", "plan", "work", "--folder", "todo")
    capsys.readouterr()
    run(repo, "list", "items", "--json")
    plan = json.loads(capsys.readouterr().out)[0]["item"]
    git(repo, "add", "planning")
    git(repo, "commit", "-qm", "a plan")
    run(repo, "worktree", "add", "foo")
    run(repo, "assign", plan, "foo")
    git(repo, "add", "planning")
    git(repo, "commit", "-qm", "assigned")
    wt = repo / "worktrees" / "foo"
    branch_answers = wt / "planning" / "answers" / f"{plan}.json"
    answers.update(branch_answers, plan, lambda d: d["comments"].append({"id": "c1", "on": "", "text": "hi", "at": "t"}))
    git(wt, "add", "planning")
    git(wt, "commit", "-qm", "answered on the branch")
    git(repo, "merge", "-q", "--no-edit", "foo")
    assert answers.read(repo / "planning" / "answers" / f"{plan}.json", plan)["comments"][0]["text"] == "hi"
    assert stored(repo, plan)["worktree"]["name"] == "foo"
    assert "worktree" not in answers.read(repo / "planning" / "answers" / f"{plan}.json", plan)


def test_an_assignment_in_an_answers_file_moves_to_its_file(repo, capsys):
    run(repo, "worktree", "add", "foo")
    run(repo, "new", "plan", "work", "--folder", "todo")
    capsys.readouterr()
    run(repo, "list", "items", "--json")
    plan = json.loads(capsys.readouterr().out)[0]["item"]
    path = repo / "planning" / "answers" / f"{plan}.json"
    answers.update(path, plan, lambda d: d.update(worktree={"name": "foo", "at": "t", "by": "test"}))
    capsys.readouterr()
    run(repo, "list", "items", "--worktree", "foo", "--json")
    assert json.loads(capsys.readouterr().out)[0]["item"] == plan
    assert stored(repo, plan)["worktree"] == {"name": "foo", "at": "t", "by": "test"}
    assert "worktree" not in json.loads(path.read_text(encoding="utf-8"))


def test_check_reads_the_assignments_file(repo, capsys):
    path = repo / "planning" / "assignments.json"
    path.write_text('{"format": 1, "plans": {"p-zzzzzz": {"name": "foo"}}}', encoding="utf-8")
    assert run(repo, "check") == 0
    out = capsys.readouterr().out
    assert "assignments.json" in out and "p-zzzzzz" in out and "does not belong" not in out
    path.write_text("not json", encoding="utf-8")
    assert run(repo, "check") == 1
    assert "cannot be read" in capsys.readouterr().out


def test_hidden_wins_over_a_class_that_sets_display():
    """The lanes' filter hides cards with `hidden`, and `.item-card` sets display:
    picking a worktree changed nothing on the page."""
    css = (Path(service.__file__).parent / "static" / "app.css").read_text(encoding="utf-8")
    assert "[hidden] { display: none !important; }" in css

