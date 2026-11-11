"""A project's worktree folder: planning.toml, this machine's registry, the default."""

import json
import threading
from pathlib import Path

import pytest

from tests.test_planning import make_project, run
from tests.test_planning_service import req
from waterrise import check, client, folder, layout, service, types
from waterrise.guide import PATH as GUIDE, rendered


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv(client.PORT_ENV, raising=False)
    for name in ("WATERRISE_PROJECT", "WATERRISE_PLANNING_DIR", "WATERRISE_PROJECT_NAME"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


@pytest.fixture
def proj(env):
    root = make_project(env / "proj").resolve()
    assert run(root, "init") == 0
    return root


def set_toml(root: Path, value: str | None) -> None:
    cfg = root / "planning" / "planning.toml"
    lines = [x for x in cfg.read_text(encoding="utf-8").splitlines() if not x.startswith("worktrees")]
    if value is not None:
        lines.append(f"worktrees = {json.dumps(value)}")
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")


def register(root: Path, **more) -> None:
    path = client.state_dir() / client.REGISTRY
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"path": str(root), "planning": str(root / "planning"), "name": root.name, "slug": root.name, **more}
    path.write_text(json.dumps({"repos": [entry]}), encoding="utf-8")


def test_the_default_is_worktrees(proj):
    s = layout.setting(proj)
    assert (s.rel, s.path, s.source, s.problem) == ("worktrees", proj / "worktrees", "default", None)
    assert s.inside


def test_planning_toml_sets_a_folder_inside(proj):
    set_toml(proj, ".worktrees")
    s = layout.setting(proj)
    assert (s.rel, s.path, s.source) == (".worktrees", proj / ".worktrees", "planning.toml")
    assert s.inside


def test_a_folder_beside_the_checkout(proj):
    set_toml(proj, "../proj-wt/")
    s = layout.setting(proj)
    assert (s.rel, s.path, s.source) == ("../proj-wt", proj.parent / "proj-wt", "planning.toml")
    assert not s.inside


def test_the_registry_overrides_planning_toml_and_dropping_it_brings_it_back(proj):
    set_toml(proj, ".worktrees")
    register(proj, worktrees=".claude/worktrees")
    s = layout.setting(proj)
    assert (s.rel, s.source) == (".claude/worktrees", "registry")
    register(proj)                       # what --default leaves
    assert (layout.setting(proj).rel, layout.setting(proj).source) == (".worktrees", "planning.toml")


@pytest.mark.parametrize("value", ["", "  ", "/abs/wt", "C:\\wt", "C:/wt", "~/wt", "a\\b", ".", "./", ".git/wt", 3])
def test_refused_values(proj, value):
    assert layout.invalid(value)
    set_toml(proj, value) if isinstance(value, str) else \
        (proj / "planning" / "planning.toml").write_text('format = 1\nworktrees = 3\n', encoding="utf-8")
    s = layout.setting(proj)
    assert (s.rel, s.source) == ("worktrees", "default")
    assert s.problem and "planning.toml" in s.problem


def test_check_reports_an_invalid_setting(proj):
    set_toml(proj, "/abs/wt")
    rep = check.run(folder.read(proj / "planning", proj), git=False)
    assert [f for f in rep.of("error") if f.file == "planning.toml" and "absolute" in f.message]


def test_check_accepts_a_valid_setting(proj):
    set_toml(proj, "../proj-wt")
    rep = check.run(folder.read(proj / "planning", proj), git=False)
    assert not rep.of("error")


def test_the_key_is_declared_and_the_guide_is_current():
    assert "worktrees" in types.CONFIG_SPEC.fields
    text = GUIDE.read_text(encoding="utf-8")
    assert rendered(text) == text
    assert "`worktrees`" in text


def test_the_service_route_sets_and_drops_the_override(env, proj):
    srv = service.make_server(0, client.state_dir())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        status, entry = client.call(port, "POST", "/api/repos",
                                    {"path": str(proj), "planning": str(proj / "planning"), "name": "demo"}, auth=True)
        assert status == 200
        running = client.Running(port, {})
        client.set_worktrees(running, "demo", ".wt")
        assert (layout.setting(proj).rel, layout.setting(proj).source) == (".wt", "registry")
        with pytest.raises(Exception, match="absolute"):
            client.set_worktrees(running, "demo", "/abs")
        assert layout.setting(proj).rel == ".wt", "a refused value changes nothing"
        assert req(port, "PUT", "/api/repos/demo/worktrees", {"worktrees": ".x"})[0] == 403, "needs the token"
        client.set_worktrees(running, "demo", None)
        assert layout.setting(proj).source == "default"
        # registering again keeps a machine's setting
        client.set_worktrees(running, "demo", ".wt")
        client.call(port, "POST", "/api/repos",
                    {"path": str(proj), "planning": str(proj / "planning"), "name": "demo"}, auth=True)
        assert layout.setting(proj).rel == ".wt"
    finally:
        srv.shutdown()
        srv.server_close()


# ---- everything follows the setting (s2-2) --------------------------------------- #

from tests.test_worktrees import git, status  # noqa: E402


@pytest.fixture
def tracked(env):
    """A repository with a tracked planning folder and a plan in todo/."""
    root = (env / "repo").resolve()
    root.mkdir()
    git(root, "init", "-q")
    assert run(root, "init") == 0
    (root / "code").write_text("x\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "first")
    return root


def plan_id(root, capsys) -> str:
    run(root, "new", "plan", "work", "--folder", "todo")
    capsys.readouterr()
    run(root, "list", "items", "--json")
    return json.loads(capsys.readouterr().out)[0]["item"]


def test_add_inside_writes_its_own_rule(tracked, capsys):
    set_toml(tracked, ".worktrees")
    git(tracked, "commit", "-qam", "the folder")
    assert run(tracked, "worktree", "add", "foo") == 0
    assert (tracked / ".worktrees" / "foo" / "code").exists()
    assert "/.worktrees/" in (tracked / ".gitignore").read_text(encoding="utf-8")
    git(tracked, "check-ignore", ".worktrees/x")


def test_add_beside_writes_no_rule_and_leaves_the_checkout_clean(tracked, capsys):
    set_toml(tracked, "../repo-wt")
    git(tracked, "commit", "-qam", "the folder")
    before = (tracked / ".gitignore").read_text(encoding="utf-8")
    assert run(tracked, "worktree", "add", "foo") == 0
    assert (tracked.parent / "repo-wt" / "foo" / "code").exists()
    assert (tracked / ".gitignore").read_text(encoding="utf-8") == before
    assert status(tracked) == ""
    capsys.readouterr()
    assert run(tracked, "check") == 0
    assert "is not ignored" not in capsys.readouterr().out


def test_the_registry_override_is_where_add_puts_it(tracked):
    register(tracked, worktrees=".mine")
    assert run(tracked, "worktree", "add", "foo") == 0
    assert (tracked / ".mine" / "foo").is_dir() and not (tracked / "worktrees").exists()


def test_worktree_list_says_which_folder_and_where_it_is_set(tracked, capsys):
    set_toml(tracked, ".worktrees")
    capsys.readouterr()
    assert run(tracked, "worktree", "list") == 0
    assert ".worktrees/ (planning.toml)" in capsys.readouterr().out


def test_changing_the_folder_leaves_an_assigned_plan_gone_and_check_says_why(tracked, capsys):
    plan = plan_id(tracked, capsys)
    assert run(tracked, "worktree", "add", "foo") == 0
    assert run(tracked, "assign", plan, "foo") == 0
    set_toml(tracked, ".elsewhere")
    capsys.readouterr()
    run(tracked, "check")
    out = capsys.readouterr().out
    assert "foo" in out and "no longer exists" in out and ".elsewhere/ (planning.toml)" in out
    assert "git worktree move" in out
    # never silently unassigned, and the old worktree is still listed with --all
    run(tracked, "worktree", "list", "--all")
    out = capsys.readouterr().out
    assert "foo" in out and "not assignable" in out
    # moving it over brings the plan back
    (tracked / ".elsewhere").mkdir()      # git worktree move wants the parent to exist
    git(tracked, "worktree", "move", str(tracked / "worktrees" / "foo"), str(tracked / ".elsewhere" / "foo"))
    capsys.readouterr()
    run(tracked, "check")
    assert "no longer exists" not in capsys.readouterr().out
