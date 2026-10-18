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
def systemd(tmp_path, monkeypatch):
    """A HOME, a fake pip, and a `systemctl` that logs its arguments and keeps
    the unit's active state in a file."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "WATERRISE_PROJECT", "WATERRISE_PORT"):
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
""")
    (bin_ / "loginctl").write_text("#!/bin/sh\necho Linger=no\n")
    for f in bin_.iterdir():
        f.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}{os.pathsep}{os.environ['PATH']}")

    def create(path: Path) -> None:
        (path / "bin").mkdir(parents=True)
        (path / "bin" / "python").symlink_to(sys.executable)

    def pip(path: Path, checkout: Path) -> None:
        script = path / "bin" / "waterrise"
        script.write_text(f"#!/bin/sh\necho 'waterrise {__version__}'\n")
        script.chmod(0o755)

    monkeypatch.setattr(install, "create_venv", create)
    monkeypatch.setattr(install, "pip_install", pip)
    monkeypatch.setattr(install, "_venv_works", lambda path: (path / "bin" / "python").exists())
    monkeypatch.setattr(client, "find", lambda port=None: None)
    return log


def calls(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


def test_boot_writes_enables_and_starts_the_unit(systemd, capsys):
    assert cli.main(["--no-color", "install", "--boot"]) == 0
    unit = Path(os.environ["HOME"]) / ".config" / "systemd" / "user" / "waterrise.service"
    text = unit.read_text()
    assert f"ExecStart={install.venv_script()} service run" in text
    assert "WantedBy=default.target" in text and "Restart=on-failure" in text
    assert f"Environment=XDG_STATE_HOME={os.environ['XDG_STATE_HOME']}" in text
    assert calls(systemd) == ["--user daemon-reload", "--user enable waterrise.service",
                              "--user restart waterrise.service"]
    assert "loginctl enable-linger someone" in capsys.readouterr().out


def test_boot_again_with_the_same_unit_does_not_restart(systemd):
    assert cli.main(["--no-color", "install", "--boot"]) == 0
    systemd.write_text("")
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

    systemd.write_text("")
    monkeypatch.setattr(client, "find", running("2026.1.1"))
    assert cli.main(["--no-color", "install"]) == 0
    assert "--user restart waterrise.service" in calls(systemd)
    systemd.write_text("")
    monkeypatch.setattr(client, "find", running("2099.1.1"))
    assert cli.main(["--no-color", "install"]) == 0
    assert "--user restart waterrise.service" not in calls(systemd)


def test_service_run_serves_in_the_foreground(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("WATERRISE_")}
    env |= {"XDG_STATE_HOME": str(tmp_path / "state"), "WATERRISE_PORT": "0",
            "PYTHONPATH": str(REPO)}
    proc = subprocess.Popen([sys.executable, "-m", "waterrise", "service", "run"], cwd=tmp_path,
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        info = tmp_path / "state" / "waterrise" / "service.json"
        deadline = time.monotonic() + 5
        while not info.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        import json
        port = json.loads(info.read_text())["port"]
        assert client.health(port)["service"] == "waterrise"
    finally:
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=5)
    assert proc.returncode == 0, err
    assert "serving on" in err and "stopped" in err
    assert not info.exists()
