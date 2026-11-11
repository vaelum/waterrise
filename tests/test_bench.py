"""tools/bench.py runs, on a small project, on every platform the suite runs on."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import bench  # noqa: E402


def test_bench_runs_on_a_small_project(tmp_path, capsys):
    out = tmp_path / "bench.json"
    bench.main(["--runs", "1", "--plans", "4", "--worktrees", "2", "--living", "1",
                "--protocol-mb", "0.05", "--json", str(out)])
    result = json.loads(out.read_text(encoding="utf-8"))
    rows = {r["what"]: r for r in result["synthetic"]}
    assert set(rows) >= {"waterrise status", "waterrise check", "waterrise worktree list --all",
                         "page: repo_view deep", "change poll: one tick"}
    assert all(r["exit"] == 0 for r in rows.values()), rows
    # git is counted in the child process, on every platform
    assert rows["waterrise worktree list --all"]["git"] > 0
    assert "== synthetic" in capsys.readouterr().out
