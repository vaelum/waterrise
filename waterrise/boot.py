"""`waterrise install --boot`: a systemd user unit that runs the service at login.

    ~/.config/systemd/user/waterrise.service
        ExecStart=<venv>/bin/waterrise service run

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

from . import ui
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
    out = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)
    if check and out.returncode != 0:
        raise WaterriseError(f"systemctl --user {' '.join(args)} failed",
                             hint=(out.stderr or out.stdout).strip() or None)
    return out


def installed() -> bool:
    return unit_path().exists()


def active() -> bool:
    return installed() and systemctl("is-active", "--quiet", UNIT, check=False).returncode == 0


def _linger_hint() -> None:
    user = os.environ.get("USER") or ""
    if not user or shutil.which("loginctl") is None:
        return
    out = subprocess.run(["loginctl", "show-user", user, "--property=Linger"],
                         capture_output=True, text=True)
    if out.stdout.strip() == "Linger=no":
        ui.warn("note:", "the service starts at login and stops at logout; to keep it running "
                         f"without a session: loginctl enable-linger {user}")


def enable(ctx: Project, executable: Path) -> None:
    """Write the unit, and enable and (re)start it."""
    from . import client

    path, text = unit_path(), unit_text(executable)
    changed = not path.exists() or path.read_text() != text
    if ctx.would(f"write {path} and enable --now {UNIT}"):
        return
    # A detached service from `serve` holds the port the unit wants.
    running = client.find()
    if running and not active():
        client.stop(running)
        ui.ok("stopped", f"the detached service on port {running.port}; the unit takes over")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
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
    if ctx.would(f"restart {UNIT} for {version}"):
        return
    systemctl("restart", UNIT)
    ui.ok("restarted", f"{UNIT}: {have} → {version}")


def disable(ctx: Project) -> None:
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
