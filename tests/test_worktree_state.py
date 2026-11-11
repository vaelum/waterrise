"""Worktree state: the main HEAD once, the rest in a pool (p-pw7ft8 s3-2)."""

import json
import subprocess

from tests.test_planning import run
from tests.test_worktrees import entry, env, git, register, repo, wt_folder, wtf  # noqa: F401 (fixtures)
from waterrise import context, service, worktrees


def add(repo, *names):
    for n in names:
        assert run(repo, "worktree", "add", n) == 0


def test_a_worktree_that_does_not_answer_is_unknown_not_clean(repo, capsys, monkeypatch):
    add(repo, "foo", "bar")
    slow = wtf(repo) / "bar"
    real = worktrees._git

    def git_(cwd, *args):
        if cwd == slow and args[:1] == ("status",):
            return None                 # what _git returns on a timeout
        return real(cwd, *args)
    monkeypatch.setattr(worktrees, "_git", git_)
    capsys.readouterr()
    assert run(repo, "worktree", "list", "--json") == 0
    rows = {r["name"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows["bar"]["clean"] is None and rows["foo"]["clean"] is True
    assert run(repo, "worktree", "list") == 0
    line = next(x for x in capsys.readouterr().out.splitlines() if " bar " in x)
    assert "unknown" in line and "clean" not in line
    register(entry(repo))
    view = service.repo_view(entry(repo), deep=True)
    trees = {w["name"]: w for w in view["worktrees"]}
    assert trees["bar"]["clean"] is None and trees["foo"]["clean"] is True


def test_git_processes_stay_within_two_per_worktree(repo, capsys, monkeypatch):
    add(repo, *[f"w{n}" for n in range(6)])
    (wtf(repo) / "w3" / "dirt").write_text("x", encoding="utf-8")
    git(wtf(repo) / "w4", "commit", "--allow-empty", "-qm", "ahead")
    calls = []
    real = subprocess.run

    def counting(args, *a, **k):
        if args and args[0] == "git":
            calls.append(args)
        return real(args, *a, **k)
    monkeypatch.setattr(subprocess, "run", counting)
    ctx = context.resolve(repo)
    trees = [w for w in worktrees.read(ctx.root) if w.assignable]
    calls.clear()
    got = worktrees.states(ctx.root, trees)
    monkeypatch.setattr(subprocess, "run", real)
    assert len(trees) == 7
    assert len(calls) <= 2 * len(trees), calls
    assert not any(c[3:5] == ["rev-parse", "HEAD"] for c in calls), "the main HEAD comes from worktree list"
    by = {w.name: got[w.path] for w in trees}
    assert by["w3"]["clean"] is False and by["w4"]["ahead"] == 1 and by["main"]["clean"] is True
    # the same answers as asking one at a time
    assert by == {w.name: worktrees.state(ctx.root, w) for w in trees}
