"""`waterrise install --boot` / `--no-boot` and `service run`, with a fake systemctl."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from waterrise import __main__ as cli
from waterrise import __version__, boot, client, install

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def systemd(tmp_path, fake_home, monkeypatch):
    """A HOME, a fake pip, and a `systemctl` that logs its arguments and keeps
    the unit's active state in a file."""
    if os.name == "nt":
        pytest.skip("systemd and /bin/sh stand-ins; Windows' --boot is tested in test_platform.py")
    fake_home(tmp_path / "home")
    for name in ("WATERRISE_PROJECT", "WATERRISE_PORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("USER", "someone")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "systemctl.log"
    flag = tmp_path / "active"
    (bin_ / "systemctl").write_text(f"""#!/bin/sh
echo "$@" >> {log}
case "$2" in
  restart|start) touch {flag} ;;
  disable) rm -f {flag} ;;
  is-active) [ -e {flag} ] ;;
esac
""", encoding="utf-8")
    (bin_ / "loginctl").write_text("#!/bin/sh\necho Linger=no\n", encoding="utf-8")
    for f in bin_.iterdir():
        f.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}{os.pathsep}{os.environ['PATH']}")

    def create(path: Path) -> None:
        (path / "bin").mkdir(parents=True)
        (path / "bin" / "python").symlink_to(sys.executable)

    def pip(path: Path, checkout: Path) -> None:
        script = path / "bin" / "waterrise"
        script.write_text(f"#!/bin/sh\necho 'waterrise {__version__}'\n", encoding="utf-8")
        script.chmod(0o755)

    monkeypatch.setattr(install, "create_venv", create)
    monkeypatch.setattr(install, "pip_install", pip)
    monkeypatch.setattr(install, "_venv_works", lambda path: (path / "bin" / "python").exists())
    monkeypatch.setattr(client, "find", lambda port=None: None)
    return log


def calls(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_boot_writes_enables_and_starts_the_unit(systemd, capsys):
    assert cli.main(["--no-color", "install", "--boot"]) == 0
    unit = Path(os.environ["HOME"]) / ".config" / "systemd" / "user" / "waterrise.service"
    text = unit.read_text(encoding="utf-8")
    assert f"ExecStart={install.venv_script()} service run" in text
    assert "WantedBy=default.target" in text and "Restart=on-failure" in text
    assert f"Environment=XDG_STATE_HOME={os.environ['XDG_STATE_HOME']}" in text
    assert calls(systemd) == ["--user daemon-reload", "--user enable waterrise.service",
                              "--user restart waterrise.service", "--user is-active --quiet waterrise.service"]
    assert "loginctl enable-linger someone" in capsys.readouterr().out


def test_boot_again_with_the_same_unit_does_not_restart(systemd):
    assert cli.main(["--no-color", "install", "--boot"]) == 0
    systemd.write_text("", encoding="utf-8")
    assert cli.main(["--no-color", "install", "--boot"]) == 0
    assert "--user restart waterrise.service" not in calls(systemd)


def test_no_boot_disables_and_removes_the_unit(systemd):
    assert cli.main(["--no-color", "install", "--boot"]) == 0
    assert cli.main(["--no-color", "install", "--no-boot"]) == 0
    assert not boot.unit_path().exists()
    assert "--user disable --now waterrise.service" in calls(systemd)
    # The venv and the launcher stay: --no-boot is only about the unit.
    assert install.venv_script().exists()


def test_uninstall_removes_the_unit_too(systemd):
    assert cli.main(["--no-color", "install", "--boot"]) == 0
    assert cli.main(["--no-color", "install", "--uninstall"]) == 0
    assert not boot.unit_path().exists() and not install.venv_dir().exists()


def test_an_install_restarts_only_an_older_running_unit(systemd, monkeypatch):
    assert cli.main(["--no-color", "install", "--boot"]) == 0

    def running(version):
        return lambda port=None: client.Running(8765, {"version": f"{version}-abcdef12"})

    systemd.write_text("", encoding="utf-8")
    monkeypatch.setattr(client, "find", running("2026.1.1"))
    assert cli.main(["--no-color", "install"]) == 0
    assert "--user restart waterrise.service" in calls(systemd)
    systemd.write_text("", encoding="utf-8")
    monkeypatch.setattr(client, "find", running("2099.1.1"))
    assert cli.main(["--no-color", "install"]) == 0
    assert "--user restart waterrise.service" not in calls(systemd)


def _foreground(tmp_path, monkeypatch):
    """`waterrise service run` in a process of its own, and its service.json."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))   # this process's client too
    env = {k: v for k, v in os.environ.items() if not k.startswith("WATERRISE_")}
    env |= {"WATERRISE_PORT": "0", "PYTHONPATH": str(REPO), "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen([sys.executable, "-m", "waterrise", "service", "run"], cwd=tmp_path,
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    info = tmp_path / "state" / "waterrise" / "service.json"
    deadline = time.monotonic() + 5
    while not info.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    return proc, info


def test_service_run_serves_in_the_foreground(tmp_path, monkeypatch):
    # Stopped as `service stop` does it, through the API: on Windows a signal
    # is TerminateProcess, and nothing would run on the way out.
    proc, info = _foreground(tmp_path, monkeypatch)
    try:
        import json
        port = json.loads(info.read_text(encoding="utf-8"))["port"]
        h = client.health(port)
        assert h["service"] == "waterrise"
        client.stop(client.Running(port, h))
    finally:
        # stop() returns when service.json is gone, a moment before the
        # process has exited: give it that moment before killing it.
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate(timeout=5)
    assert proc.returncode == 0, err
    assert "serving on" in err and "stopped" in err
    assert not info.exists()


@pytest.mark.skipif(not hasattr(signal, "SIGKILL"), reason="POSIX: systemd stops the unit with SIGTERM")
def test_service_run_stops_cleanly_on_sigterm(tmp_path, monkeypatch):
    proc, info = _foreground(tmp_path, monkeypatch)
    try:
        assert info.exists()
    finally:
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=5)
    assert proc.returncode == 0, err
    assert "serving on" in err and "stopped" in err
    assert not info.exists()


def test_boot_again_restarts_an_older_running_service(systemd, monkeypatch):
    """--boot with an unchanged unit, while the unit's service is an older
    version: the install must still restart it (it did not, 2026.10.1)."""
    assert cli.main(["--no-color", "install", "--boot"]) == 0
    systemd.write_text("", encoding="utf-8")
    monkeypatch.setattr(client, "find", lambda port=None: client.Running(8765, {"version": "2026.1.1-abcdef12"}))
    assert cli.main(["--no-color", "install", "--boot"]) == 0
    assert "--user restart waterrise.service" in calls(systemd)
