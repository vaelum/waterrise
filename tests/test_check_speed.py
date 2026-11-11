"""check's faster paths answer exactly as the slower ones did (p-pw7ft8 s3-1)."""

import random
import re
import subprocess
from pathlib import Path

from tests.test_planning import make_project, run
from tests.test_worktrees import git
from waterrise import check, folder
from waterrise.folder import Item
from waterrise.types import Problem

ROOT = Path(__file__).resolve().parents[1]


def old_line_of(text: str, problem: Problem) -> int | None:
    """Item.line_of before s3-1, verbatim."""
    if problem.line:
        return problem.line
    lines = text.splitlines()
    if problem.local:
        pat = re.compile(r'^\s*id\s*=\s*"' + re.escape(problem.local) + r'"')
        for i, line in enumerate(lines, 1):
            if pat.match(line):
                return i
    if problem.where:
        key = re.split(r"[.\[]", problem.where)[0]
        pat = re.compile(r"^\s*\[{0,2}" + re.escape(key) + r"\b")
        for i, line in enumerate(lines, 1):
            if pat.match(line):
                return i
    return None


ODD = '''#:schema x
  id = "s1"
[[phase]]
[[[phase]]]
[ phase]
phase-x = 1
phase_y = 2
-dash = 3
  [[decision.option]]
  id = "with space"
id="tight"
été = 4
[scope]
'''


def test_line_of_answers_as_before_for_every_plan_here_and_odd_text():
    texts = [p.read_text(encoding="utf-8") for p in sorted((ROOT / "planning").rglob("*.toml"))] + [ODD]
    rnd = random.Random(7)
    compared = 0
    for text in texts:
        it = Item(path=Path("x.toml"), rel="x.toml", folder="draft", text=text)
        words = sorted(set(re.findall(r"[\w-]+", text)) | {"", "-", "_", "phase", "nope"})
        locals_ = sorted(set(re.findall(r'id\s*=\s*"([^"]*)"', text)) | {"", "nope"})
        wheres = words + ["." + w for w in words[:15]] + ["[" + w for w in words[:15]] + ["phase.step[0]", "a[1].b"]
        for loc in locals_:
            for where in rnd.sample(wheres, min(len(wheres), 25)):
                p = Problem("warn", "m", local=loc, where=where)
                assert it.line_of(p) == old_line_of(text, p), (loc, where)
                compared += 1
    assert compared > 1000


def old_ids_at_head(f: folder.Folder) -> dict[str, str] | None:
    """check._ids_at_head before s3-1: a git show per file."""
    rel = f.root.relative_to(f.repo).as_posix()
    names = subprocess.run(["git", "-C", str(f.repo), "ls-tree", "-r", "--name-only", "HEAD", "--", rel],
                           capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
    out = {}
    for name in names.stdout.splitlines():
        if not name.endswith(".toml") or name.endswith("/planning.toml") or name == "planning.toml":
            continue
        shown = subprocess.run(["git", "-C", str(f.repo), "show", f"HEAD:{name}"], capture_output=True,
                               text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
        m = re.search(r'^id\s*=\s*"([^"]*)"', shown.stdout, re.M)
        if shown.returncode == 0 and m:
            out[name[len(rel) + 1:] if rel != "." else name] = m.group(1)
    return out


def test_ids_at_head_in_two_git_processes_as_before(tmp_path, monkeypatch):
    root = make_project(tmp_path / "repo")
    git(root, "init", "-q")
    assert run(root, "init") == 0
    for n in range(12):
        assert run(root, "new", "plan", f"plan-{n}") == 0
    # a name git would quote, a CRLF file, and one with no id
    (root / "planning" / "draft" / "with space é.toml").write_text('kind = "plan"\nid = "p-zzzzzz"\n', encoding="utf-8")
    (root / "planning" / "draft" / "crlf.toml").write_bytes(b'kind = "plan"\r\nid = "p-yyyyyy"\r\n')
    (root / "planning" / "notes" / "no-id.toml").write_text('kind = "note"\n', encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "plans")
    (root / "planning" / "draft" / "crlf.toml").unlink()        # gone from the work tree, still at HEAD
    f = folder.read(root / "planning", root)

    calls = []
    real = subprocess.run

    def counting(args, *a, **k):
        if args and args[0] == "git":
            calls.append(args)
        return real(args, *a, **k)
    monkeypatch.setattr(check.subprocess, "run", counting)
    new = check._ids_at_head(f)
    monkeypatch.setattr(check.subprocess, "run", real)
    assert len(calls) == 2
    old = old_ids_at_head(f)
    # The same, except a name git quotes (non-ASCII), which the old way never found.
    assert "draft/with space é.toml" not in old
    assert {k: v for k, v in new.items() if k != "draft/with space é.toml"} == old
    assert new["draft/with space é.toml"] == "p-zzzzzz" and new["draft/crlf.toml"] == "p-yyyyyy"
    assert len(new) == 14
