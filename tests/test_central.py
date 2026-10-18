"""The two homes: a project planning in its repository or in the central one."""

import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from tests.test_planning import plan_text, run
from tests.test_planning_service import req
from waterrise import central, client, folder, service
from waterrise import __main__ as cli


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    for name in ("WATERRISE_PROJECT", "WATERRISE_PLANNING_DIR", "WATERRISE_PROJECT_NAME", "WATERRISE_PROG"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


@pytest.fixture
def server(env, monkeypatch):
    """A service on a free port, which `client.find` and `ensure` use."""
    srv = service.make_server(0, client.state_dir())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    running = client.Running(port, client.health(port))
    monkeypatch.setattr(client, "ensure", lambda port=None: (running, "running"))
    monkeypatch.setattr(client, "find", lambda port=None: running)
    yield port
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def project(env):
    root = env / "x"
    root.mkdir()
    git(root, "init", "-q")
    return root


def in_(root: Path, *argv: str) -> int:
    old = Path.cwd()
    os.chdir(root)
    try:
        return cli.main(["--no-color", *argv])
    finally:
        os.chdir(old)


def test_a_central_project_plans_in_the_central_repository(server, project):
    assert in_(project, "projects", "add", str(project), "--home", "central") == 0
    planning = central.planning_for("x")
    assert (planning / "planning.toml").exists()
    assert in_(project, "new", "plan", "t") == 0
    assert list((planning / "draft").glob("t.toml"))
    assert sorted(os.listdir(project)) == [".git"]          # nothing in the project's own repository
    log = git(central.root(), "log", "--format=%s")
    assert "x: new plan t" in log and "x: a typed planning folder" in log
    assert git(central.root(), "status", "--porcelain") == ""
    (entry,) = client.registry()
    assert entry["home"] == "central" and entry["planning"] == str(planning)


def test_an_edit_is_committed_by_the_next_check(server, project):
    assert in_(project, "projects", "add", str(project), "--home", "central") == 0
    (central.planning_for("x") / "draft" / "a.toml").write_text(plan_text())
    assert in_(project, "check") == 0
    assert git(central.root(), "log", "-1", "--format=%s").strip() == "x: check"
    assert git(central.root(), "status", "--porcelain") == ""


def test_a_page_answer_on_a_central_project_is_committed(server, project):
    assert in_(project, "projects", "add", str(project), "--home", "central") == 0
    (central.planning_for("x") / "draft" / "a.toml").write_text(plan_text())
    central.commit("x", "x: the plan")
    status, _, _ = req(server, "PUT", "/api/r/x/decision/p-4k7x2m/alerts", {"selected": ["mail"]})
    assert status == 200
    # The commit follows the reply.
    for _ in range(100):
        if git(central.root(), "log", "-1", "--format=%s").strip() != "x: the plan":
            break
        time.sleep(0.02)
    assert git(central.root(), "log", "-1", "--format=%s").strip() == "x: decision p-4k7x2m/alerts (the page)"
    assert "answers/p-4k7x2m.json" in git(central.root(), "show", "--name-only", "--format=", "HEAD")
    assert git(central.root(), "status", "--porcelain") == ""


def test_a_move_in_a_central_folder_is_a_git_mv_in_the_central_repository(server, project):
    assert in_(project, "projects", "add", str(project), "--home", "central") == 0
    planning = central.planning_for("x")
    (planning / "draft" / "a.toml").write_text(plan_text())
    central.commit("x", "x: the plan")
    f = folder.read(planning, central.root())
    f.move(f.get("p-4k7x2m"), "todo")
    assert "R  x/planning/draft/a.toml -> x/planning/todo/a.toml" in git(central.root(), "status", "--porcelain")


def test_an_unregistered_clone_without_planning_is_told_both_ways(env, project, capsys):
    assert in_(project, "status") == 1
    err = capsys.readouterr().err
    assert "init, to plan in this repository" in err
    assert f"projects add {project} --home central" in err


def test_a_registered_project_keeps_its_home(server, project):
    assert in_(project, "projects", "add", str(project), "--home", "central") == 0
    status, body = client.call(server, "POST", "/api/repos",
                               {"path": str(project), "planning": str(project / "planning"), "home": "repo"},
                               auth=True)
    assert status == 409 and "central home already" in body["error"]


def test_a_repo_project_is_registered_as_before(server, env):
    root = env / "r"
    root.mkdir()
    git(root, "init", "-q")
    assert run(root, "init") == 0
    assert in_(root, "projects", "add", str(root)) == 0
    (entry,) = client.registry()
    assert entry["home"] == "repo" and entry["planning"] == str(root / "planning")
    assert not central.root().exists()


def test_projects_lists_the_home(server, project, capsys):
    assert in_(project, "projects", "add", str(project), "--home", "central") == 0
    capsys.readouterr()
    assert in_(project, "projects") == 0
    out = capsys.readouterr().out
    assert "central" in out and str(central.planning_for("x")) in out


def _repo_project(env: Path) -> Path:
    root = env / "r"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.org")
    git(root, "config", "user.name", "t")
    assert run(root, "init") == 0
    (root / "planning" / "draft" / "a.toml").write_text(plan_text())
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "a plan")
    return root


def test_projects_home_carries_the_folder_there_and_back(server, env):
    root = _repo_project(env)
    assert in_(root, "projects", "add", str(root)) == 0
    status, _, _ = req(server, "PUT", "/api/r/r/decision/p-4k7x2m/alerts", {"selected": ["mail"]})
    assert status == 200
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "the answer")

    assert in_(root, "projects", "home", "r", "central") == 0
    assert not (root / "planning").exists()
    assert git(root, "status", "--porcelain") == ""
    assert git(root, "log", "-1", "--format=%s").strip() == "planning: the plans moved to waterrise's central home"
    moved = central.planning_for("r")
    assert (moved / "draft" / "a.toml").exists() and (moved / "answers" / "p-4k7x2m.json").exists()
    assert git(central.root(), "status", "--porcelain") == ""
    status, item, _ = req(server, "GET", "/api/r/r/item/p-4k7x2m")
    assert status == 200 and item["answers"]["decisions"]["alerts"]["selected"] == ["mail"]
    assert in_(root, "status", "p-4k7x2m") == 0

    assert in_(root, "projects", "home", "r", "repo") == 0
    assert (root / "planning" / "answers" / "p-4k7x2m.json").exists()
    assert not central.planning_for("r").parent.exists()
    assert git(root, "status", "--porcelain") == "" and git(central.root(), "status", "--porcelain") == ""
    status, item, _ = req(server, "GET", "/api/r/r/item/p-4k7x2m")
    assert status == 200 and item["answers"]["decisions"]["alerts"]["selected"] == ["mail"]
    (entry,) = client.registry()
    assert entry["home"] == "repo" and entry["planning"] == str(root / "planning")


def test_projects_home_refuses_while_an_answers_file_is_locked(server, env, capsys):
    import fcntl

    root = _repo_project(env)
    assert in_(root, "projects", "add", str(root)) == 0
    assert req(server, "PUT", "/api/r/r/decision/p-4k7x2m/alerts", {"selected": ["mail"]})[0] == 200
    with open(root / "planning" / "answers" / ".p-4k7x2m.json.lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        assert in_(root, "projects", "home", "r", "central") == 1
    assert "is writing p-4k7x2m.json" in capsys.readouterr().err
    assert (root / "planning" / "draft" / "a.toml").exists() and not central.planning_for("r").exists()
