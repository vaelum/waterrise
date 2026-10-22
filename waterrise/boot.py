"""`waterrise install --boot`: the service at login.

On Linux, a systemd user unit:

    ~/.config/systemd/user/waterrise.service
        ExecStart=<venv>/bin/waterrise service run

On Windows, a value in the current user's Run key (no administrator needed),
with the venv's pythonw.exe so no console window opens:

    HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run\\waterrise
        "<venv>\\Scripts\\pythonw.exe" -P -m waterrise service run

Nothing restarts it there if it dies; `serve`, and every command that needs
the service, starts it again.

`service run` is the same service `serve` starts detached, in the foreground,
so systemd owns the process. `serve` run while it is up finds it and registers
with it, as with any running service. `loginctl enable-linger` (the service
also before login, and after logout) is printed as a hint, never run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from . import dirs, ui, windows
from .context import Project
from .errors import WaterriseError

UNIT = "waterrise.service"
# Passed into the unit when set at install time: the user manager does not
# see the login shell's environment, and a service with another state dir or
# port than the CLI's would be a second, invisible service.
CARRIED_ENV = ("XDG_STATE_HOME", "XDG_DATA_HOME", "WATERRISE_PORT")


def unit_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user" / UNIT


def unit_text(executable: Path) -> str:
    env = "".join(f"Environment={name}={os.environ[name]}\n"
                  for name in CARRIED_ENV if os.environ.get(name))
    return f"""# Written by `waterrise install --boot`; `waterrise install --no-boot` removes it.
[Unit]
Description=waterrise: the planning service and its pages on 127.0.0.1

[Service]
ExecStart={executable} service run
{env}Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""


def systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    if shutil.which("systemctl") is None:
        raise WaterriseError("systemctl not found: --boot needs systemd's user manager")
    out = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, encoding="utf-8",
                         stdin=subprocess.DEVNULL)
    if check and out.returncode != 0:
        raise WaterriseError(f"systemctl --user {' '.join(args)} failed",
                             hint=(out.stderr or out.stdout).strip() or None)
    return out


def installed() -> bool:
    if dirs.WINDOWS:
        return windows.run_command() is not None
    return unit_path().exists()


def active() -> bool:
    if dirs.WINDOWS:
        from . import client
        return installed() and client.find() is not None
    return installed() and systemctl("is-active", "--quiet", UNIT, check=False).returncode == 0


# --------------------------------------------------------------------------- #
# Windows: the Run value
# --------------------------------------------------------------------------- #

RUN_KEY_SHOWN = rf"HKCU\{windows.RUN_KEY}\{windows.RUN_NAME}"


def run_argv(executable: Path) -> list[str]:
    """What login runs: the venv's pythonw beside `executable` (its waterrise.exe)."""
    argv = [str(executable.with_name("pythonw.exe")), "-P", "-m", "waterrise", "service", "run"]
    if os.environ.get("WATERRISE_PORT"):
        argv += ["--port", os.environ["WATERRISE_PORT"]]
    return argv


def _launch(argv: list[str]) -> None:
    """Start what login starts, detached, now."""
    from . import client
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, **client._detached())


def _enable_windows(ctx: Project, executable: Path) -> None:
    from . import client

    argv = run_argv(executable)
    command = subprocess.list2cmdline(argv)
    changed = windows.run_command() != command
    if ctx.would(f"write {RUN_KEY_SHOWN} and start the service"):
        return
    windows.set_run_command(command)
    running = client.find()
    if running and changed:
        client.stop(running)
    if changed or not running:
        _launch(argv)
    ui.ok("start at login", f"{RUN_KEY_SHOWN}: {command}")
    carried = [n for n in ("XDG_STATE_HOME", "XDG_DATA_HOME") if os.environ.get(n)]
    if carried:
        ui.warn("note:", f"{' and '.join(carried)} are set here, and the service at login sees them only "
                         "if they are user environment variables (setx), not just this shell's")


def _restart_windows(ctx: Project, running, have: str, version: str) -> None:
    from . import client
    from .install import venv_script

    if ctx.would(f"restart the service for {version}"):
        return
    client.stop(running)
    _launch(run_argv(venv_script()))
    ui.ok("restarted", f"the service: {have} → {version}")


def _disable_windows(ctx: Project) -> None:
    from . import client

    if not installed():
        ui.ok("start at login", "is not set up")
        return
    if ctx.would(f"remove {RUN_KEY_SHOWN} and stop the service"):
        return
    windows.delete_run_command()
    running = client.find()
    if running:
        client.stop(running)
    ui.ok("removed", RUN_KEY_SHOWN)


def _linger_hint() -> None:
    user = os.environ.get("USER") or ""
    if not user or shutil.which("loginctl") is None:
        return
    out = subprocess.run(["loginctl", "show-user", user, "--property=Linger"],
                         capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
    if out.stdout.strip() == "Linger=no":
        ui.warn("note:", "the service starts at login and stops at logout; to keep it running "
                         f"without a session: loginctl enable-linger {user}")


def enable(ctx: Project, executable: Path) -> None:
    """Write the unit, and enable and (re)start it."""
    from . import client

    if dirs.WINDOWS:
        _enable_windows(ctx, executable)
        return
    path, text = unit_path(), unit_text(executable)
    changed = not path.exists() or path.read_text(encoding="utf-8") != text
    if ctx.would(f"write {path} and enable --now {UNIT}"):
        return
    # A detached service from `serve` holds the port the unit wants.
    running = client.find()
    if running and not active():
        client.stop(running)
        ui.ok("stopped", f"the detached service on port {running.port}; the unit takes over")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    systemctl("daemon-reload")
    systemctl("enable", UNIT)
    if changed or not active():
        systemctl("restart", UNIT)
    ui.ok(UNIT, f"enabled and running: {path}")
    _linger_hint()


def restart_if_older(ctx: Project, version: str) -> None:
    """After an install: restart the unit's service when it runs an older version."""
    from . import client
    from .install import parse

    if not active():
        return
    running = client.find()
    if running is None:
        return                          # starting, or just restarted: nothing to compare
    have = running.version.split("-")[0]
    try:
        newer = parse(have) >= parse(version)
    except WaterriseError:
        newer = False                   # a pre-calendar version: older
    if newer:
        return
    if dirs.WINDOWS:
        _restart_windows(ctx, running, have, version)
        return
    if ctx.would(f"restart {UNIT} for {version}"):
        return
    systemctl("restart", UNIT)
    ui.ok("restarted", f"{UNIT}: {have} → {version}")


def disable(ctx: Project) -> None:
    if dirs.WINDOWS:
        _disable_windows(ctx)
        return
    path = unit_path()
    if not path.exists():
        ui.ok(UNIT, "is not installed")
        return
    if ctx.would(f"disable --now {UNIT} and remove {path}"):
        return
    systemctl("disable", "--now", UNIT, check=False)
    path.unlink()
    systemctl("daemon-reload", check=False)
    ui.ok("removed", f"{UNIT} ({path})")
