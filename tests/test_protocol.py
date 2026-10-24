"""A step's protocol: notes, commands' output, the cap and the masking."""

import io
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from waterrise import protocol


@pytest.fixture
def proto(tmp_path):
    planning = tmp_path / "planning"
    planning.mkdir()
    (planning / ".gitignore").write_text("answers/.*.lock\n", encoding="utf-8")
    return protocol.Protocol(planning, "p-4k7x2m", "s1-1")


def py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_the_path_and_the_ignore_line(proto, tmp_path):
    assert proto.path == tmp_path / "planning" / "protocols" / "p-4k7x2m" / "s1-1.md"
    assert (tmp_path / "planning" / ".gitignore").read_text(encoding="utf-8") == "answers/.*.lock\nprotocols/\n"
    protocol.ensure_ignored(tmp_path / "planning")          # only once
    assert (tmp_path / "planning" / ".gitignore").read_text(encoding="utf-8").count("protocols/") == 1


def test_a_note_and_a_run_in_the_documented_shape(proto):
    proto.note("Starting s1-1: running the tests first.")
    out = io.StringIO()
    rc = proto.run(py("import sys; print('one'); print('two — ✓'); sys.exit(3)"), echo=out)
    text = proto.path.read_text(encoding="utf-8")
    assert rc == 3 and out.getvalue().replace("\r\n", "\n") == "one\ntwo — ✓\n"
    assert "\n## " in text and " note\nStarting s1-1: running the tests first.\n" in text
    run = text[text.index(" run\n"):]
    lines = run.splitlines()
    assert lines[1] == protocol.FENCE and lines[2].startswith("$ ") and "import sys" in lines[2]
    assert "one\ntwo — ✓\n" in run.replace("\r\n", "\n")
    assert f"{protocol.FENCE}\nexit 3 after " in run and run.endswith(" s\n")
    assert b"\r\n" not in proto.path.read_bytes()


def test_a_command_that_cannot_start(proto):
    assert proto.run(["waterrise-no-such-program-xyz"], echo=io.StringIO()) == 127
    assert "could not start" in proto.path.read_text(encoding="utf-8")


def test_output_without_a_last_newline_still_closes_the_fence(proto):
    proto.run(py("import sys; sys.stdout.write('no newline')"), echo=io.StringIO())
    assert f"no newline\n{protocol.FENCE}\nexit 0" in proto.path.read_text(encoding="utf-8")


def test_past_the_cap_a_command_keeps_its_last_lines_and_notes_stay_whole(proto, monkeypatch):
    monkeypatch.setattr(protocol, "CAP", 2000)
    monkeypatch.setattr(protocol, "TAIL", 5)
    proto.run(py("for i in range(500): print(f'line {i:03d}')"), echo=io.StringIO())
    text = proto.path.read_text(encoding="utf-8")
    assert "line 000" in text and "line 499" in text and "line 250" not in text
    assert "lines left out: the protocol is over" in text
    assert [ln for ln in text.splitlines() if ln.startswith("line ")][-5:] == [f"line {i}" for i in range(495, 500)]
    proto.note("x" * 3000)                                  # a note is never cut
    assert "x" * 3000 in proto.path.read_text(encoding="utf-8")


def test_secrets_are_masked_before_anything_is_written(proto, monkeypatch):
    (Path.home() / ".secrets").write_text("FORGEJO_TOKEN=abcdef123456\nSHORT=abc\n", encoding="utf-8")
    monkeypatch.setenv("SOME_API_KEY", "zyxwvu987654")
    p = protocol.Protocol(proto.path.parents[2], "p-4k7x2m", "s1-2")
    p.note("the token is abcdef123456, the key zyxwvu987654, and abc stays")
    p.run(py("print('abcdef123456')"), echo=io.StringIO())
    text = p.path.read_text(encoding="utf-8")
    assert "abcdef123456" not in text and "zyxwvu987654" not in text
    assert "<redacted FORGEJO_TOKEN>" in text and "<redacted SOME_API_KEY>" in text and "abc stays" in text


WRITER = """
import sys
from pathlib import Path
from waterrise import protocol
p = protocol.Protocol(Path(sys.argv[1]), "p-4k7x2m", "s1-1")
for i in range(50):
    p.note(f"{sys.argv[2]} {i}")
"""


def test_two_processes_writing_one_protocol_lose_nothing(proto):
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)}
    procs = [subprocess.Popen([sys.executable, "-c", WRITER, str(proto.path.parents[2]), who], env=env,
                              stdin=subprocess.DEVNULL) for who in ("a", "b")]
    assert [p.wait(60) for p in procs] == [0, 0]
    text = proto.path.read_text(encoding="utf-8")
    assert sum(1 for ln in text.splitlines() if ln[:2] in ("a ", "b ")) == 100


# --------------------------------------------------------------------------- #
# the commands
# --------------------------------------------------------------------------- #

@pytest.fixture
def proj(tmp_path):
    from tests.test_planning import make_project, plan_text, put, run
    root = make_project(tmp_path / "proj")
    assert run(root, "init") == 0
    put(root, "draft/a.toml", plan_text())
    return root


def cli(root, *argv, stdin=None):
    env = {**os.environ, "WATERRISE_PROJECT": str(root), "PYTHONPATH": str(Path(__file__).resolve().parent.parent)}
    return subprocess.run([sys.executable, "-m", "waterrise", "--no-color", *argv], cwd=root, env=env,
                          input=stdin, capture_output=True, encoding="utf-8", timeout=60)


def test_note_and_run_write_the_steps_protocol(proj):
    assert cli(proj, "note", "p-4k7x2m", "s1-1", "Starting:", "the", "baseline").returncode == 0
    assert cli(proj, "note", "p-4k7x2m", "s1-1", stdin="from stdin\n").returncode == 0
    out = cli(proj, "run", "p-4k7x2m", "s1-1", "--", sys.executable, "-c", "print('measured'); raise SystemExit(2)")
    assert out.returncode == 2 and out.stdout.strip() == "measured"
    text = (proj / "planning" / "protocols" / "p-4k7x2m" / "s1-1.md").read_text(encoding="utf-8")
    assert "Starting: the baseline" in text and "from stdin" in text and "measured\n" in text and "exit 2 after" in text
    assert "protocols/" in (proj / "planning" / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_a_long_running_command_is_in_the_protocol_before_it_ends(proj):
    env = {**os.environ, "WATERRISE_PROJECT": str(proj), "PYTHONPATH": str(Path(__file__).resolve().parent.parent)}
    # The words are built at run time: the command line itself is in the protocol.
    code = "import sys, time; print('fir' + 'st', flush=True); time.sleep(3); print('la' + 'st')"
    p = subprocess.Popen([sys.executable, "-m", "waterrise", "run", "p-4k7x2m", "s1-2", "--", sys.executable, "-c", code],
                         cwd=proj, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    path = proj / "planning" / "protocols" / "p-4k7x2m" / "s1-2.md"
    import time
    deadline = time.monotonic() + 2.5
    while time.monotonic() < deadline and not (path.exists() and "first" in path.read_text(encoding="utf-8")):
        time.sleep(0.05)
    assert "first" in path.read_text(encoding="utf-8") and "last" not in path.read_text(encoding="utf-8")
    assert p.wait(30) == 0 and "last" in path.read_text(encoding="utf-8")


def test_stdin_is_passed_through_to_the_command(proj):
    out = cli(proj, "run", "p-4k7x2m", "s1-1", "--", sys.executable, "-c", "print(input()[::-1])", stdin="abc\n")
    assert out.returncode == 0 and out.stdout.strip() == "cba"


@pytest.mark.parametrize("argv, says", [
    (["note", "p-zzzzzz", "s1-1", "x"], "no item with the id p-zzzzzz"),
    (["note", "p-4k7x2m", "s9-9", "x"], "p-4k7x2m has no step s9-9"),
    (["note", "p-4k7x2m", "s1-3", "x"], "is the user's"),
    (["run", "p-4k7x2m", "s1-1"], "no command to run"),
])
def test_what_note_and_run_refuse(proj, argv, says):
    out = cli(proj, *argv)
    assert out.returncode == 1 and says in out.stderr


def test_protocols_belong_in_a_planning_folder(proj):
    assert cli(proj, "note", "p-4k7x2m", "s1-1", "x").returncode == 0
    out = cli(proj, "check")
    assert out.returncode == 0 and "does not belong" not in out.stdout + out.stderr


def test_check_warns_about_a_done_agent_step_without_a_protocol(proj):
    plan = proj / "planning" / "draft" / "a.toml"
    text = plan.read_text(encoding="utf-8")
    # s1-1 and s1-2 done (s1-3 is the user's, and never warned about).
    done = text.replace('  id = "s1-1"\n', '  id = "s1-1"\n  state = "done"\n  commit = "abc"\n', 1)
    done = done.replace('  id = "s1-2"\n', '  id = "s1-2"\n  state = "done"\n  commit = "abc"\n', 1)
    plan.write_text(done, encoding="utf-8")
    assert "protocol" not in cli(proj, "check").stdout             # no protocols here at all: nothing said
    assert cli(proj, "run", "p-4k7x2m", "s1-1", "--", sys.executable, "-c", "print(1)").returncode == 0
    out = cli(proj, "check").stdout
    assert "step s1-1 is done, but its protocol has no note" in out
    assert "step s1-2 is done, but has no protocol" in out
    assert cli(proj, "note", "p-4k7x2m", "s1-1", "measured it").returncode == 0
    assert cli(proj, "note", "p-4k7x2m", "s1-2", "nothing to run").returncode == 0
    out = cli(proj, "check").stdout
    assert "has no protocol" not in out and "has no note" not in out


def test_check_finds_a_credential_in_a_protocol(proj):
    p = proj / "planning" / "protocols" / "p-4k7x2m" / "s1-1.md"
    p.parent.mkdir(parents=True)
    p.write_text("## 2026-10-24 10:00:00 note\nthe key is ghp_" + "A" * 36 + "\n", encoding="utf-8")
    out = cli(proj, "check")
    assert out.returncode == 1 and "protocols/p-4k7x2m/s1-1.md" in out.stdout and "credential" in out.stdout


# --------------------------------------------------------------------------- #
# the page
# --------------------------------------------------------------------------- #

@pytest.fixture
def served(proj, tmp_path, monkeypatch):
    from waterrise import client, service
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    srv = service.make_server(0, client.state_dir())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    status, _ = client.call(port, "POST", "/api/repos",
                            {"path": str(proj), "planning": str(proj / "planning"), "name": "demo"}, auth=True)
    assert status == 200
    yield port
    srv.shutdown()
    srv.server_close()


def test_the_page_gets_each_steps_protocol(proj, served):
    from tests.test_planning_service import req
    assert cli(proj, "note", "p-4k7x2m", "s1-1", "Measuring the baseline.\nThen the rest.").returncode == 0
    assert cli(proj, "run", "p-4k7x2m", "s1-1", "--", sys.executable, "-c", "print('42 MB/s')").returncode == 0
    status, plan, headers = req(served, "GET", "/api/r/demo/item/p-4k7x2m")
    p = plan["protocols"]["s1-1"]
    assert p["entries"] == 2 and p["last_note"] == "Measuring the baseline.\nThen the rest." and p["last"]
    status, body, _ = req(served, "GET", "/api/r/demo/protocol/p-4k7x2m/s1-1")
    assert status == 200 and "42 MB/s" in body["text"] and body["summary"]["entries"] == 2
    # A new entry changes the plan's ETag, so an open page reloads it.
    tag = headers["ETag"]
    assert cli(proj, "note", "p-4k7x2m", "s1-1", "Done.").returncode == 0
    assert req(served, "GET", "/api/r/demo/item/p-4k7x2m")[2]["ETag"] != tag


@pytest.mark.parametrize("path", ["/api/r/demo/protocol/p-4k7x2m/s9-9", "/api/r/demo/protocol/p-4k7x2m/..",
                                  "/api/r/demo/protocol/p-4k7x2m/%2e%2e%2fplanning",
                                  "/api/r/demo/protocol/..%2fanswers/s1-1"])
def test_only_a_protocol_is_served(proj, served, path):
    from tests.test_planning_service import req
    assert req(served, "GET", path)[0] == 404


def test_a_python_child_writes_utf8_into_the_protocol(proto, monkeypatch):
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    proto.run(py("import sys; print(sys.flags.utf8_mode, 'Grüße — ✓')"), echo=io.StringIO())
    assert "1 Grüße — ✓" in proto.path.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name != "nt", reason="Windows' own commands, in the Windows VM")
def test_windows_commands_into_a_protocol(proto):
    assert proto.run(["cmd", "/c", "echo hello from cmd"], echo=io.StringIO()) == 0
    assert proto.run(["powershell", "-NoProfile", "-Command", "Write-Output 'Grüße'; exit 4"], echo=io.StringIO()) == 4
    text = proto.path.read_text(encoding="utf-8")
    assert "hello from cmd" in text and "Grüße" in text and "exit 4 after" in text


def test_crlf_output_is_written_with_lf(proto):
    proto.run(py("import sys; sys.stdout.buffer.write(b'a\\r\\nb\\r\\n')"), echo=io.StringIO())
    assert b"a\nb\n" in proto.path.read_bytes() and b"\r" not in proto.path.read_bytes()
