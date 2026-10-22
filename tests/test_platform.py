"""What waterrise does differently on Windows, and what keeps a test run inside its temp dirs."""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from waterrise import boot, central, client, install, lock
from waterrise.errors import WaterriseError

REPO = Path(__file__).resolve().parent.parent


def test_every_default_directory_is_inside_the_tests_home(tmp_path_factory):
    base = Path(tmp_path_factory.getbasetemp()).resolve()
    home = Path.home().resolve()
    assert home.is_relative_to(base)
    for var in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA"):
        assert Path(os.environ[var]).resolve().is_relative_to(base), var
    for path in (install.data_dir(), install.launcher(), central.root(), client.state_dir(), boot.unit_path()):
        assert path.resolve().is_relative_to(home), path


def test_a_held_lock_excludes_until_released(tmp_path, locks):
    path = tmp_path / "x.lock"
    first = lock.acquire(path)
    with pytest.raises(BlockingIOError):
        lock.acquire(path, wait=False)
    got = []
    waiter = threading.Thread(target=lambda: got.append(lock.acquire(path)))
    waiter.start()
    time.sleep(0.1)
    assert not got                      # still waiting
    lock.release(first)
    waiter.join(5)
    assert got
    lock.release(got[0])
    with lock.held(path, wait=False):
        pass


def test_the_windows_branch_retries_and_unlocks(tmp_path, windows_locks):
    path = tmp_path / "x.lock"
    first = lock.acquire(path)
    threading.Timer(0.05, lock.release, args=(first,)).start()
    with lock.held(path):
        pass
    m = windows_locks
    assert m.calls.count(m.LK_NBLCK) > 2 and m.calls.count(m.LK_UNLCK) == 2


def test_no_locking_at_all_is_an_error_not_a_skipped_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(lock, "fcntl", None)
    monkeypatch.setattr(lock, "msvcrt", None)
    with pytest.raises(WaterriseError, match="no file locking"):
        lock.acquire(tmp_path / "x.lock")


def test_the_service_start_lock_is_the_shared_one(locks):
    fh = client._lock()
    try:
        with pytest.raises(BlockingIOError):
            lock.acquire(client.state_dir() / "start.lock", wait=False)
    finally:
        lock.release(fh)


def _flaky_replace(monkeypatch, failures):
    real = os.replace
    calls = []

    def fake(src, dst):
        calls.append(dst)
        if len(calls) <= failures:
            raise PermissionError(13, "The process cannot access the file")
        real(src, dst)
    monkeypatch.setattr(lock.os, "replace", fake)
    return calls


def test_a_replace_waits_out_a_reader_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(lock, "WINDOWS", True)
    calls = _flaky_replace(monkeypatch, failures=2)
    from waterrise import answers
    path = tmp_path / "answers" / "p-4k7x2m.json"
    answers.update(path, "p-4k7x2m", lambda d: d["decisions"].__setitem__("q", {"selected": ["a"]}))
    assert len(calls) == 3 and answers.read(path)["decisions"]["q"]


def test_a_replace_gives_up_in_the_end(tmp_path, monkeypatch):
    monkeypatch.setattr(lock, "WINDOWS", True)
    monkeypatch.setattr(lock, "REPLACE_FOR", 0.05)
    _flaky_replace(monkeypatch, failures=10**6)
    (tmp_path / "a").write_text("x", encoding="utf-8")
    with pytest.raises(PermissionError):
        lock.replace(tmp_path / "a", tmp_path / "b")


def test_a_replace_is_not_retried_on_posix(tmp_path, monkeypatch):
    monkeypatch.setattr(lock, "WINDOWS", False)
    calls = _flaky_replace(monkeypatch, failures=1)
    (tmp_path / "a").write_text("x", encoding="utf-8")
    with pytest.raises(PermissionError):
        lock.replace(tmp_path / "a", tmp_path / "b")
    assert len(calls) == 1


WRITER = """
import sys, time
from pathlib import Path
from waterrise import answers
path, go, who = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
while not go.exists():
    time.sleep(0.001)
for i in range(10):
    answers.update(path, "p-4k7x2m", lambda d: d["decisions"].__setitem__(f"{who}{i}", {"selected": ["a"]}))
"""


def test_two_processes_writing_one_answers_file_lose_nothing(tmp_path):
    path = tmp_path / "answers" / "p-4k7x2m.json"
    go = tmp_path / "go"
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)}
    procs = [subprocess.Popen([sys.executable, "-c", WRITER, str(path), str(go), who], env=env)
             for who in ("a", "b")]
    time.sleep(0.3)                     # both started and waiting
    go.touch()
    assert [p.wait(30) for p in procs] == [0, 0]
    from waterrise import answers
    assert len(answers.read(path)["decisions"]) == 20


def _text_io_without_encoding(root: Path):
    """Every open / read_text / write_text / Popen(text=True) / subprocess.run(text=True)
    in the package that leaves the encoding to the platform."""
    import ast
    found = []
    for f in sorted(root.rglob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            kw = {k.arg: k.value for k in node.keywords}
            if "encoding" in kw:
                continue
            if name == "open" and not isinstance(func, ast.Attribute) or name == "fdopen":
                mode = node.args[1] if len(node.args) > 1 else kw.get("mode")
                if isinstance(mode, ast.Constant) and "b" in str(mode.value):
                    continue
                if node.args and isinstance(node.args[0], ast.Attribute) and node.args[0].attr == "devnull":
                    continue
            elif name in ("read_text", "write_text"):
                pass
            elif name in ("run", "Popen", "check_output") and isinstance(kw.get("text"), ast.Constant) \
                    and kw["text"].value is True:
                pass
            else:
                continue
            found.append(f"{f.relative_to(root.parent)}:{node.lineno} {name}")
    return found


def test_every_text_read_and_write_names_its_encoding():
    package = Path(install.__file__).resolve().parent
    assert _text_io_without_encoding(package) == []
    assert _text_io_without_encoding(package.parent / "tests") == []


def waterrise_in(root: Path, *argv: str, **env: str) -> subprocess.CompletedProcess:
    """`python -m waterrise <argv>` in its own process, with a default-encoding
    open an error rather than a warning."""
    env = {**os.environ, "WATERRISE_PROJECT": str(root), "PYTHONPATH": str(REPO), **env}
    return subprocess.run([sys.executable, "-X", "warn_default_encoding", "-W", "error::EncodingWarning",
                           "-m", "waterrise", "--no-color", *argv], cwd=root, env=env,
                          capture_output=True, encoding="utf-8", errors="replace", timeout=60)


def test_the_commands_never_read_or_write_in_the_platform_encoding(tmp_path):
    (tmp_path / "butler").mkdir()
    (tmp_path / "butler" / "butler.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    plan = tmp_path / "plan.md"
    plan.write_text("# Fix the dates — all of them\n\nWhy: ✓ is not ✗.\n\n## Steps\n\n"
                    "- [ ] **Change the pages — eleven.** (amos)\n", encoding="utf-8")
    for argv in (["init"], ["new", "plan", "dates"], ["new", "report", "zahlen"], ["convert", str(plan)],
                 ["check"], ["status"], ["list"], ["guide"]):
        out = waterrise_in(tmp_path, *argv)
        assert out.returncode == 0 and "EncodingWarning" not in out.stderr, (argv, out.stdout, out.stderr)
    converted = next((tmp_path / "planning").rglob("plan.toml"))
    assert "— all of them" in converted.read_text(encoding="utf-8")


def test_files_that_are_committed_are_written_with_lf():
    # Python writes "\n" as CRLF on Windows unless told otherwise. Only the
    # machine-local files (the service token, install's source note, the
    # systemd unit) may be written either way.
    import ast
    package = Path(install.__file__).resolve().parent
    local = {"client.py", "install.py", "boot.py"}
    missing = []
    for f in sorted(package.rglob("*.py")):
        if f.name in local:
            continue
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name)):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
                writes = name == "write_text" or name == "fdopen" and len(node.args) > 1 \
                    and isinstance(node.args[1], ast.Constant) and "w" in node.args[1].value
                if writes and not any(k.arg == "newline" for k in node.keywords):
                    missing.append(f"{f.name}:{node.lineno}")
    assert missing == []


def test_a_check_mark_on_a_cp1252_console_is_replaced_not_an_error(monkeypatch):
    import io
    from waterrise import ui
    raw = io.BytesIO()
    class Console(io.TextIOWrapper):        # into a pipe it would be UTF-8 instead
        def isatty(self):
            return True
    out = Console(raw, encoding="cp1252", newline="\n")
    monkeypatch.setattr(sys, "stdout", out)
    ui.init(no_color=True)
    ui.ok("✓", "checked — fine")
    out.flush()
    assert raw.getvalue() == b"? checked \x97 fine\n"      # the em dash exists in cp1252, the check mark not


def test_the_commands_run_on_a_cp1252_console(tmp_path):
    (tmp_path / "butler").mkdir()
    (tmp_path / "butler" / "butler.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    for argv in (["init"], ["new", "report", "zahlen"], ["check"], ["guide"], ["status"]):
        out = waterrise_in(tmp_path, *argv, PYTHONIOENCODING="cp1252")
        assert out.returncode == 0, (argv, out.stdout, out.stderr)


def test_the_guide_compares_equal_from_a_crlf_checkout(tmp_path, monkeypatch):
    from waterrise import guide
    crlf = tmp_path / "GUIDE.md"
    crlf.write_bytes(guide.PATH.read_bytes().replace(b"\n", b"\r\n"))
    text = crlf.read_text(encoding="utf-8")
    assert text == guide.rendered(text)


def test_the_schema_line_has_forward_slashes_on_windows(tmp_path, monkeypatch):
    from waterrise import commands
    monkeypatch.setattr(commands.os.path, "relpath", lambda path, start: r"..\..\.schema\v1.json")
    assert commands._schema_ref(tmp_path / "planning" / "todo" / "a.toml", tmp_path / "planning") \
        == "../../.schema/v1.json"


def test_the_work_plan_server_takes_gits_and_pythons_windows_paths_as_one():
    import ntpath
    from waterrise import mcp
    forms = ["C:/Users/amos/proj", r"c:\Users\amos\proj", "C:\\Users\\Amos\\proj\\"]
    assert len({mcp._norm(f, ntpath) for f in forms}) == 1


def test_a_file_outside_any_repository_says_what_git_said(tmp_path):
    from waterrise import mcp
    item = tmp_path / "a.toml"
    item.write_text('kind = "plan"\nid = "p-abc123"\n', encoding="utf-8")
    with pytest.raises(mcp.PlanError, match="not inside a git repository .*(not a git repository|git did not run)"):
        mcp.resolve(str(item))


@pytest.mark.parametrize("windows", [False, True])
def test_the_data_and_state_directories(windows, monkeypatch, tmp_path):
    from waterrise import dirs
    monkeypatch.setattr(dirs, "WINDOWS", windows)
    home, local = Path.home(), Path(os.environ["LOCALAPPDATA"])
    if windows:
        assert dirs.data() == local / "waterrise" and dirs.state() == local / "waterrise" / "state"
    else:
        assert dirs.data() == home / ".local" / "share" / "waterrise"
        assert dirs.state() == home / ".local" / "state" / "waterrise"
    assert install.data_dir() == dirs.data() and client.state_dir() == dirs.state()
    assert central.root() == dirs.data() / "plans"
    # The XDG variables win on both, and the service's own directory over them.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    assert dirs.data() == tmp_path / "d" / "waterrise" and dirs.state() == tmp_path / "s" / "waterrise"
    monkeypatch.setenv(dirs.STATE_ENV, str(tmp_path / "own"))
    assert dirs.state() == tmp_path / "own"


def test_without_localappdata_windows_falls_back_under_the_profile(monkeypatch):
    from waterrise import dirs
    monkeypatch.setattr(dirs, "WINDOWS", True)
    monkeypatch.delenv("LOCALAPPDATA")
    assert dirs.data() == Path.home() / "AppData" / "Local" / "waterrise"


@pytest.mark.parametrize("windows", [False, True])
def test_the_venv_paths_and_symlinks(windows, monkeypatch, tmp_path):
    from waterrise import dirs
    monkeypatch.setattr(dirs, "WINDOWS", windows)
    v = install.venv_dir()
    if windows:
        assert install.venv_python(v) == v / "Scripts" / "python.exe"
        assert install.venv_script() == v / "Scripts" / "waterrise.exe"
    else:
        assert install.venv_python(v) == v / "bin" / "python"
        assert install.venv_script() == v / "bin" / "waterrise"
    made = {}

    class Builder:
        def __init__(self, **kw):
            made.update(kw)

        def create(self, path):
            made["path"] = path
    monkeypatch.setattr(install.venv, "EnvBuilder", Builder)
    install.create_venv(tmp_path / "v")
    assert made["symlinks"] is (not windows) and made["path"] == tmp_path / "v"
    ran = []
    monkeypatch.setattr(install.subprocess, "run", lambda argv, **kw: ran.append(argv) or
                        subprocess.CompletedProcess(argv, 0))
    install.pip_install(tmp_path / "v", tmp_path / "src")
    assert ran[0][0] == str(install.venv_python(tmp_path / "v"))


@pytest.fixture
def on_windows(monkeypatch, registry, tmp_path):
    """install as it runs on Windows: Scripts\\*.exe in the venv, a fake pip,
    the registry in a dictionary, and no systemd."""
    from waterrise import dirs
    monkeypatch.setattr(dirs, "WINDOWS", True)
    monkeypatch.delenv("WATERRISE_PROJECT", raising=False)

    # Stand-ins that run on Windows too: no symlink (a standard user can't
    # make one), and the script's version is answered, not run.
    def create(path):
        (path / "Scripts").mkdir(parents=True)
        (path / "Scripts" / "python.exe").write_bytes(b"")

    def pip(path, checkout):
        (path / "Scripts" / "waterrise.exe").write_bytes(b"")
    monkeypatch.setattr(install, "create_venv", create)
    monkeypatch.setattr(install, "pip_install", pip)
    monkeypatch.setattr(install, "_venv_works", lambda path: (path / "Scripts" / "python.exe").exists())
    monkeypatch.setattr(install, "installed_version",
                        lambda: install.__version__ if install.venv_script().exists() else None)
    monkeypatch.setattr(client, "find", lambda port=None: None)     # no service running
    return registry


def run_install(*argv):
    from waterrise import __main__ as cli
    return cli.main(["--no-color", "install", *argv])


def test_install_on_windows_writes_a_cmd_launcher_and_puts_it_on_the_user_path(on_windows, capsys):
    bin_ = Path(os.environ["LOCALAPPDATA"]) / "waterrise" / "bin"
    on_windows[("Environment", "Path")] = (r"C:\Windows;%USERPROFILE%\bin", 2)
    assert run_install() == 0
    text = (bin_ / "waterrise.cmd").read_bytes()
    assert text == install.LAUNCHER_CMD.encode("ascii") and b"\r\n" in text and b"Users" not in text
    assert on_windows[("Environment", "Path")] == (rf"C:\Windows;%USERPROFILE%\bin;{bin_}", 2)
    assert on_windows["broadcasts"] == ["Environment"]
    out = capsys.readouterr().out
    assert "to your user PATH" in out and "open a new one" in out
    # Again: nothing to change, nothing announced.
    assert run_install() == 0
    assert on_windows[("Environment", "Path")][0].count("waterrise") == 1
    assert on_windows["broadcasts"] == ["Environment"]


def test_install_on_windows_with_no_user_path_yet(on_windows):
    assert run_install() == 0
    value, kind = on_windows[("Environment", "Path")]
    assert value == str(install.launcher().parent) and kind == 2


def test_install_on_windows_rewrites_its_own_old_launcher_and_refuses_a_foreign_one(on_windows, capsys):
    cmd = install.launcher()
    cmd.parent.mkdir(parents=True)
    cmd.write_bytes(f"@echo off\r\n{install.LAUNCHER_MARK}\r\n\"C:\\old\\waterrise.exe\" %*\r\n".encode("ascii"))
    assert run_install() == 0
    assert cmd.read_bytes() == install.LAUNCHER_CMD.encode("ascii")
    cmd.write_bytes(b"@echo off\r\nsomeone else's\r\n")
    assert run_install() == 1
    assert "is not waterrise's launcher" in capsys.readouterr().err
    assert cmd.read_bytes() == b"@echo off\r\nsomeone else's\r\n"


def test_install_dry_run_on_windows_says_it_would_change_the_path(on_windows, capsys):
    from waterrise import __main__ as cli
    assert cli.main(["--no-color", "--dry-run", "install"]) == 0
    assert ("Environment", "Path") not in on_windows and not install.launcher().exists()


def test_install_on_windows_can_leave_the_path_alone(on_windows):
    assert run_install("--no-path") == 0
    assert install.launcher().exists() and ("Environment", "Path") not in on_windows


def test_uninstall_on_windows_removes_the_launcher_and_the_path_entry(on_windows):
    on_windows[("Environment", "Path")] = (r"C:\Windows", 2)
    assert run_install() == 0
    assert run_install("--uninstall") == 0
    assert not install.launcher().exists() and not install.venv_dir().exists()
    assert on_windows[("Environment", "Path")] == (r"C:\Windows", 2)


def test_uninstall_on_windows_leaves_a_foreign_cmd(on_windows):
    cmd = install.launcher()
    cmd.parent.mkdir(parents=True)
    cmd.write_bytes(b"@echo off\r\nsomeone else's\r\n")
    assert run_install("--uninstall") == 0
    assert cmd.read_bytes() == b"@echo off\r\nsomeone else's\r\n"


def test_dry_runs_on_windows_change_nothing(on_windows):
    from waterrise import __main__ as cli
    assert run_install() == 0
    before = dict(on_windows)
    files = sorted(p for p in Path(os.environ["LOCALAPPDATA"]).rglob("*"))
    assert cli.main(["--no-color", "-n", "install", "--uninstall"]) == 0
    assert cli.main(["--no-color", "-n", "install"]) == 0
    assert dict(on_windows) == before and sorted(Path(os.environ["LOCALAPPDATA"]).rglob("*")) == files


@pytest.mark.parametrize("windows", [False, True])
def test_the_service_starts_detached(windows, monkeypatch, tmp_path):
    from waterrise import dirs
    monkeypatch.setattr(dirs, "WINDOWS", windows)
    kw = client._detached()
    if windows:
        assert kw == {"creationflags": 0x08 | 0x200 | 0x08000000}
        exe = tmp_path / "python.exe"
        exe.touch()
        monkeypatch.setattr(client.sys, "executable", str(exe))
        assert client._interpreter() == str(exe)                    # no pythonw beside it
        (tmp_path / "pythonw.exe").touch()
        assert client._interpreter() == str(tmp_path / "pythonw.exe")
    else:
        assert kw == {"start_new_session": True} and client._interpreter() == sys.executable


@pytest.fixture
def windows_boot(on_windows, monkeypatch):
    """--boot on Windows: what is running, what was stopped, what was launched."""
    seen = {"running": None, "stopped": [], "launched": []}
    monkeypatch.setattr(client, "find", lambda port=None: seen["running"])

    def stop(running, wait=5.0):
        seen["stopped"].append(running.version)
        seen["running"] = None
    monkeypatch.setattr(client, "stop", stop)
    monkeypatch.setattr(boot, "_launch", lambda argv: seen["launched"].append(argv))
    return seen


RUN = ("Software\\Microsoft\\Windows\\CurrentVersion\\Run", "waterrise")


def test_boot_on_windows_writes_the_run_value_and_starts_the_service(windows_boot, on_windows, capsys):
    assert run_install("--boot") == 0
    command = on_windows[RUN][0]
    pythonw = install.venv_script().with_name("pythonw.exe")
    assert command == subprocess.list2cmdline([str(pythonw), "-P", "-m", "waterrise", "service", "run"])
    assert windows_boot["launched"] == [[str(pythonw), "-P", "-m", "waterrise", "service", "run"]]
    assert "start at login" in capsys.readouterr().out
    # Again, with ours running and current: nothing restarted.
    windows_boot["running"] = client.Running(8765, {"version": install.__version__})
    assert run_install("--boot") == 0
    assert len(windows_boot["launched"]) == 1 and windows_boot["stopped"] == []


def test_install_on_windows_restarts_an_older_running_service(windows_boot, on_windows):
    assert run_install("--boot") == 0
    windows_boot["running"] = client.Running(8765, {"version": "2026.1.1-abcdef12"})
    assert run_install() == 0
    assert windows_boot["stopped"] == ["2026.1.1-abcdef12"] and len(windows_boot["launched"]) == 2


def test_no_boot_and_uninstall_on_windows_remove_the_run_value(windows_boot, on_windows, capsys):
    assert run_install("--boot") == 0
    windows_boot["running"] = client.Running(8765, {"version": install.__version__})
    assert run_install("--no-boot") == 0
    assert RUN not in on_windows and windows_boot["stopped"] == [install.__version__]
    assert run_install("--boot") == 0 and RUN in on_windows
    assert run_install("--uninstall") == 0
    assert RUN not in on_windows
    assert run_install("--no-boot") == 0
    assert "is not set up" in capsys.readouterr().out


def test_boot_on_windows_carries_the_port(windows_boot, on_windows, monkeypatch):
    monkeypatch.setenv("WATERRISE_PORT", "8800")
    assert run_install("--boot") == 0
    assert on_windows[RUN][0].endswith("service run --port 8800")



def test_this_repositorys_plan_test_skips_in_a_clone_of_the_mirror(tmp_path):
    import shutil
    for name in ("waterrise", "tests"):
        shutil.copytree(REPO / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__"))
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider",
                          "tests/test_planning.py", "-k", "this_repositorys_plan"],
                         cwd=tmp_path, capture_output=True, encoding="utf-8", timeout=120,
                         # pytest may live in the real home's user site, which the test's home hides.
                         env={**os.environ, "PYTHONPATH": os.pathsep.join(
                             [str(tmp_path), str(Path(pytest.__file__).resolve().parent.parent)])})
    assert "1 skipped" in out.stdout and "GitHub mirror" in out.stdout, out.stdout + out.stderr


def test_every_subprocess_names_its_stdin():
    # On Windows a child that inherits a pipe its parent is reading blocks on
    # it: the work-plan server's git hung for good behind the server's pending
    # read of its JSON-RPC stdin (2026-10-20, in the Windows VM).
    import ast
    package = Path(install.__file__).resolve().parent
    missing = []
    for f in sorted(package.rglob("*.py")):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in ("run", "Popen", "check_output", "call") \
                    and getattr(node.func.value, "id", "") == "subprocess" \
                    and not any(k.arg == "stdin" for k in node.keywords):
                missing.append(f"{f.name}:{node.lineno}")
    assert missing == []


def test_moving_a_home_leaves_held_locks_until_they_are_released(tmp_path, monkeypatch):
    # Windows won't delete an open file: central.move_home removes everything
    # but the answers' lock files while it holds them, and those after.
    folder = tmp_path / "planning"
    (folder / "answers").mkdir(parents=True)
    (folder / "draft").mkdir()
    (folder / "draft" / "a.toml").write_text("x", encoding="utf-8")
    (folder / "answers" / "p-4k7x2m.json").write_text("{}", encoding="utf-8")
    held = folder / "answers" / ".p-4k7x2m.json.lock"
    held.write_text("", encoding="utf-8")
    real = Path.unlink

    def unlink(self, missing_ok=False):
        if self == held:
            raise PermissionError(32, "The process cannot access the file")
        real(self, missing_ok)
    monkeypatch.setattr(Path, "unlink", unlink)
    central._remove_but_held_locks(folder)
    assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*")) == \
        ["answers", "answers/.p-4k7x2m.json.lock"]
    (folder / "draft2").mkdir()
    (folder / "draft2" / "b.toml").write_text("x", encoding="utf-8")
    monkeypatch.setattr(Path, "unlink", lambda self, missing_ok=False:
                        (_ for _ in ()).throw(PermissionError(32, "in use")))
    with pytest.raises(PermissionError):         # anything else that won't go is an error
        central._remove_but_held_locks(folder)


def test_json_into_a_pipe_is_utf8_whatever_the_code_page(tmp_path):
    # What the work-plan server reads: `status --json` through a pipe. Python
    # before 3.15 on Windows writes a pipe in cp1252; PYTHONUTF8=0 and a C
    # locale stand in for it here.
    (tmp_path / "butler").mkdir()
    (tmp_path / "butler" / "butler.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONIOENCODING"}
    env |= {"WATERRISE_PROJECT": str(tmp_path), "PYTHONPATH": str(REPO), "PYTHONUTF8": "0",
            "LC_ALL": "C", "LANG": "C", "PYTHONCOERCECLOCALE": "0"}
    run = lambda *a: subprocess.run([sys.executable, "-m", "waterrise", "--no-color", *a], cwd=tmp_path, env=env,
                                    capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
    assert run("init").returncode == 0
    assert run("new", "plan", "dates").returncode == 0
    item = next((tmp_path / "planning" / "draft").glob("*.toml"))
    item.write_text(item.read_text(encoding="utf-8").replace('title = "Dates"', 'title = "Dates — all ✓"'),
                    encoding="utf-8")
    out = run("list", "--json")
    assert out.returncode == 0, out.stderr
    assert "Dates — all ✓" in json.dumps(json.loads(out.stdout.decode("utf-8")), ensure_ascii=False)


def test_uninstall_on_windows_from_the_venv_itself_removes_it_after(on_windows, monkeypatch):
    assert run_install() == 0
    later = []
    monkeypatch.setattr(install, "_running_from", lambda path: True)
    monkeypatch.setattr(install, "_remove_after_exit", later.append)
    assert run_install("--uninstall") == 0
    assert later == [install.venv_dir()] and install.venv_dir().exists() and not install.launcher().exists()


def test_service_run_under_pythonw_logs_to_the_state_dir(tmp_path, monkeypatch):
    # pythonw.exe, as Windows' Run value starts it, has no stdout or stderr.
    from waterrise import __main__ as cli
    from waterrise import service
    seen = {}
    monkeypatch.setattr(service, "main", lambda argv: seen.update(argv=argv, err=sys.stderr) or
                        sys.stderr.write("serving\n"))
    monkeypatch.setattr(client, "find", lambda port=None: None)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    try:
        assert cli.main(["service", "run"]) == 0       # no --no-color: colour asks stdout, and there is none
    finally:
        log = sys.stderr
    assert log is not None and Path(log.name) == client.state_dir() / "service.log"
    log.close()
    assert (client.state_dir() / "service.log").read_text(encoding="utf-8") == "serving\n"


def test_a_remove_waits_out_a_reader_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(lock, "WINDOWS", True)
    f = tmp_path / "service.json"
    f.write_text("{}", encoding="utf-8")
    real, calls = Path.unlink, []

    def unlink(self, missing_ok=False):
        calls.append(self)
        if len(calls) <= 2:
            raise PermissionError(32, "The process cannot access the file")
        real(self, missing_ok)
    monkeypatch.setattr(Path, "unlink", unlink)
    lock.remove(f)
    assert len(calls) == 3 and not f.exists()
    lock.remove(f)                                  # missing is fine
