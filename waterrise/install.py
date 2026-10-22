"""`waterrise install`: a venv of its own, and a launcher on PATH.

Arch marks the system Python externally managed, so `pip install --user` is
refused; waterrise does what butler's shim does and owns a venv. Stdlib only
(venv, ensurepip, pip), so `python -m waterrise install` runs in a fresh
checkout on a bare interpreter:

    <data>/venv                        the venv, `pip install <checkout>` into it
    <data>/source                      the checkout it was installed from
    ~/.local/bin/waterrise             a symlink to the venv's script
    <data>/bin/waterrise.cmd           on Windows instead, a launcher on the user's PATH

<data> is $XDG_DATA_HOME/waterrise, or %LOCALAPPDATA%\\waterrise on Windows (dirs.py).

The install is not editable: a `git pull` takes effect on the next `install`,
which then needs no argument, because the checkout is remembered.

`--boot` starts the service at login, with a systemd user unit or on Windows a
value in the user's Run key (boot.py); `--no-boot` removes it.

`--check <version>` is what butler and description ask before they run
waterrise: exit 0 when this waterrise is at least the pin and the pin is at
least OLDEST_PIN, the oldest one it still serves; else one message naming both
versions and the command that fixes it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import venv
from pathlib import Path

from . import OLDEST_PIN, __version__, boot, dirs, ui, windows
from .context import Project
from .errors import WaterriseError

PACKAGE = Path(__file__).resolve().parent
CLONE_URL = "https://github.com/vaelum/waterrise.git"


def data_dir() -> Path:
    return dirs.data()


def venv_dir() -> Path:
    return data_dir() / "venv"


def launcher() -> Path:
    if dirs.WINDOWS:
        return data_dir() / "bin" / "waterrise.cmd"
    return Path.home() / ".local" / "bin" / "waterrise"


# The .cmd finds the venv beside it, so it holds no absolute path: cmd reads a
# batch file in the console's code page, and a user name with an umlaut in
# %LOCALAPPDATA% would not survive that.
LAUNCHER_MARK = "@rem waterrise's launcher, written by `waterrise install`"
# `(goto) 2>nul` first: cmd reads a batch file a line at a time, and after
# `install --uninstall` there is no file left to read the next line from
# ("The batch file cannot be found", exit 1). The goto drops the batch file;
# the rest of the line, %~dp0 already expanded, runs and sets the exit code.
LAUNCHER_CMD = f'@echo off\r\n{LAUNCHER_MARK}\r\n(goto) 2>nul & "%~dp0..\\venv\\Scripts\\waterrise.exe" %*\r\n'


def venv_bin(path: Path) -> Path:
    """A venv's script directory: Scripts\\ on Windows, bin/ elsewhere."""
    return path / ("Scripts" if dirs.WINDOWS else "bin")


def _exe(name: str) -> str:
    return f"{name}.exe" if dirs.WINDOWS else name


def venv_python(path: Path) -> Path:
    return venv_bin(path) / _exe("python")


def venv_script() -> Path:
    return venv_bin(venv_dir()) / _exe("waterrise")


# --------------------------------------------------------------------------- #
# versions
# --------------------------------------------------------------------------- #

# <year>.<month>.<n>[.devN], the month without a leading zero: pip would
# normalise `2026.09.1` to `2026.9.1`, and the tag and the wheel would differ.
CALVER = re.compile(r"^v?(\d{4})\.([1-9]|1[0-2])\.([1-9]\d*)(?:\.dev(\d+))?$")


def parse(version: str) -> tuple[int, int, int, float]:
    """An ordering key for a calendar version; a .devN sorts before its release."""
    m = CALVER.match(version.strip())
    if not m:
        raise WaterriseError(f"{version!r} is not a waterrise version",
                             hint="<year>.<month>.<n>, the month without a leading zero: 2026.10.1")
    dev = m.group(4)
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), float("inf") if dev is None else int(dev)


def refusal(installed: str, wanted: str, oldest: str = OLDEST_PIN) -> str | None:
    """Why `installed` will not run for a pin of `wanted`, or None if it will."""
    if parse(wanted) < parse(oldest):
        return (f"waterrise {installed} no longer serves pins before {oldest}, and this pins {wanted}")
    if parse(installed) < parse(wanted):
        return f"waterrise {installed} is installed, and this needs {wanted} or later"
    return None


def satisfies(installed: str, wanted: str, oldest: str = OLDEST_PIN) -> bool:
    return refusal(installed, wanted, oldest) is None


def how_to_install() -> str:
    src = recorded_source()
    if src:
        return f"cd {src} && git pull && python3 -m waterrise install"
    return f"git clone {CLONE_URL} && cd waterrise && python3 -m waterrise install"


# --------------------------------------------------------------------------- #
# where to install from
# --------------------------------------------------------------------------- #

def is_checkout(path: Path) -> bool:
    try:
        text = (path / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return False
    return re.search(r'^name\s*=\s*"waterrise"', text, re.M) is not None and \
        (path / "waterrise" / "__init__.py").is_file()


def recorded_source() -> Path | None:
    try:
        path = Path((data_dir() / "source").read_text(encoding="utf-8").strip())
    except OSError:
        return None
    return path if is_checkout(path) else None


def source(given: str | None) -> Path:
    """The checkout to install: --from, else the one this code runs from, else
    the one installed from last time."""
    if given:
        path = Path(given).expanduser().resolve()
        if not is_checkout(path):
            raise WaterriseError(f"{path} is not a checkout of waterrise")
        return path
    if is_checkout(PACKAGE.parent):
        return PACKAGE.parent
    path = recorded_source()
    if path:
        return path
    raise WaterriseError("no checkout of waterrise to install from",
                         hint=f"git clone {CLONE_URL} && cd waterrise && python3 -m waterrise install")


# --------------------------------------------------------------------------- #
# the steps; module-level so the tests can replace the two that need pip
# --------------------------------------------------------------------------- #

def _venv_works(path: Path) -> bool:
    py = venv_python(path)
    if not py.exists():
        return False
    try:
        return subprocess.run([str(py), "-c", "import pip"], capture_output=True, timeout=30,
                              stdin=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def create_venv(path: Path) -> None:
    # clear=True: a venv whose interpreter went away with a Python upgrade is
    # replaced, not patched. No symlinks on Windows: making one needs a
    # privilege standard users lack (WinError 1314).
    venv.EnvBuilder(with_pip=True, clear=True, symlinks=not dirs.WINDOWS).create(path)


def pip_install(path: Path, checkout: Path) -> None:
    # --force-reinstall: the version does not change between two commits, and
    # pip would otherwise call the old build satisfied. No dependencies to
    # reinstall with it.
    rc = subprocess.run([str(venv_python(path)), "-m", "pip", "install", "--quiet",
                         "--disable-pip-version-check", "--force-reinstall", "--no-deps",
                         str(checkout)], stdin=subprocess.DEVNULL).returncode
    if rc != 0:
        raise WaterriseError(f"pip could not install {checkout}", hint="its output is above")


def installed_version() -> str | None:
    script = venv_script()
    if not script.exists():
        return None
    try:
        out = subprocess.run([str(script), "--version"], capture_output=True, text=True, encoding="utf-8",
                             timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.match(r"^waterrise (\S+)", out.stdout.strip())
    return m.group(1) if out.returncode == 0 and m else None


def command() -> list[str]:
    """How a caller runs waterrise: `$WATERRISE` (an executable, or a checkout
    to run with `python -m waterrise`), else `waterrise` on PATH, else the
    launcher or the venv's script; else this package, with the interpreter
    running it."""
    import sys

    given = os.environ.get("WATERRISE")
    if given:
        path = Path(given).expanduser()
        if path.is_dir():
            return [sys.executable, "-P", "-c",
                    "import sys; sys.path.insert(0, sys.argv.pop(1)); from waterrise.__main__ import main; "
                    "sys.exit(main())", str(path.resolve())]
        return [str(path)]
    for found in (shutil.which("waterrise"), launcher(), venv_script()):
        if found and Path(found).exists():
            return [str(found)]
    return [sys.executable, "-P", "-c",
            "import sys; sys.path.insert(0, sys.argv.pop(1)); from waterrise.__main__ import main; "
            "sys.exit(main())", str(PACKAGE.parent)]


def _is_our_launcher(path: Path) -> bool:
    if dirs.WINDOWS:
        try:
            return LAUNCHER_MARK in path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return False
    return path.is_symlink() and Path(os.readlink(path)) == venv_script()


def _write_cmd_launcher(ctx: Project) -> None:
    path = launcher()
    if path.exists():
        if path.read_bytes() == LAUNCHER_CMD.encode("ascii"):
            return
        if not _is_our_launcher(path):
            raise WaterriseError(f"{path} exists and is not waterrise's launcher",
                                 hint=f"move it away, or put {venv_script().parent} on PATH yourself")
    if ctx.would(f"write {path}"):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(LAUNCHER_CMD.encode("ascii"))


def _put_on_user_path(ctx: Project, directory: Path) -> None:
    """Windows: the launcher's folder on the user's PATH, for terminals opened from now on."""
    if windows.on_user_path(directory):
        return
    if ctx.would(f"add {directory} to your user PATH"):
        return
    windows.add_to_user_path(directory)
    ui.ok("added", f"{directory} to your user PATH: terminals opened from now on find `waterrise`")


def _link_launcher(ctx: Project) -> None:
    if dirs.WINDOWS:
        _write_cmd_launcher(ctx)
        return
    link, target = launcher(), venv_script()
    if link.is_symlink() and Path(os.readlink(link)) == target:
        return
    if link.exists() or link.is_symlink():
        raise WaterriseError(f"{link} exists and is not waterrise's launcher",
                             hint=f"move it away, or put {target} on PATH yourself")
    if ctx.would(f"link {link} to {target}"):
        return
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)


def _on_path(directory: Path) -> bool:
    return any(Path(p).expanduser().resolve() == directory.resolve()
               for p in os.environ.get("PATH", "").split(os.pathsep) if p)


# --------------------------------------------------------------------------- #
# the command
# --------------------------------------------------------------------------- #

def cmd_install(ctx: Project, args) -> int:
    if args.check:
        return check(args.check)
    if args.uninstall:
        return uninstall(ctx)
    if args.no_boot:
        boot.disable(ctx)
        return 0
    checkout = source(args.from_)
    target = venv_dir()
    if not _venv_works(target):
        if ctx.would(f"create the venv {target}"):
            return 0
        ui.plain(ui.dim(f"creating the venv {target} …"))
        create_venv(target)
    if ctx.would(f"pip install {checkout} into {target}"):
        return 0
    pip_install(target, checkout)
    (data_dir() / "source").write_text(f"{checkout}\n", encoding="utf-8")
    _link_launcher(ctx)
    version = installed_version()
    if version is None:
        raise WaterriseError(f"{venv_script()} does not run after the install")
    ui.ok(f"waterrise {version}", f"installed from {checkout} into {target}")
    if dirs.WINDOWS:
        if not args.no_path:
            _put_on_user_path(ctx, launcher().parent)
        if not _on_path(launcher().parent):
            ui.plain(ui.dim(f"this terminal still has the old PATH: open a new one, or run {launcher()}"))
    elif not _on_path(launcher().parent):
        ui.warn("note:", f"{launcher().parent} is not on PATH; add it, or run {launcher()}")
    if args.boot:
        boot.enable(ctx, venv_script())
    # Whether or not --boot wrote the unit just now: an unchanged unit is not
    # restarted by enable, and its service may still be the older version.
    boot.restart_if_older(ctx, version)
    return 0


def check(wanted: str) -> int:
    why = refusal(__version__, wanted)
    if why is None:
        ui.ok(f"waterrise {__version__}", f"satisfies {wanted}")
        return 0
    if parse(wanted) < parse(OLDEST_PIN):
        ui.err(why, f"bump the pin (its vendor/waterrise submodule) to {OLDEST_PIN} or later")
    else:
        ui.err(why, how_to_install())
    return 1


def _running_from(venv_path: Path) -> bool:
    import sys
    try:
        return Path(sys.prefix).resolve() == venv_path.resolve()
    except OSError:
        return False


def _remove_after_exit(path: Path) -> None:
    """Windows won't delete a running .exe: `waterrise install --uninstall`
    through the launcher runs from the venv it removes (WinError 5, 2026-10-21,
    in the VM). A hidden PowerShell retries for half a minute after we end."""
    from . import client
    quoted = str(path).replace("'", "''")
    script = (f"Start-Sleep -Seconds 1; foreach ($i in 1..30) {{ try {{ Remove-Item -LiteralPath '{quoted}' "
              f"-Recurse -Force -ErrorAction Stop; break }} catch {{ Start-Sleep -Seconds 1 }} }}")
    subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, **client._detached())


def uninstall(ctx: Project) -> int:
    """The unit, the launcher and the venv. The registry and the plans stay."""
    boot.disable(ctx)
    link = launcher()
    if _is_our_launcher(link):
        if not ctx.would(f"remove {link}"):
            link.unlink()
            ui.ok("removed", str(link))
    if dirs.WINDOWS and windows.on_user_path(link.parent) and not ctx.would(
            f"remove {link.parent} from your user PATH"):
        windows.remove_from_user_path(link.parent)
        ui.ok("removed", f"{link.parent} from your user PATH")
    target = venv_dir()
    if target.exists() and not ctx.would(f"remove {target}"):
        if dirs.WINDOWS and _running_from(target):
            _remove_after_exit(target)
            ui.ok("removing", f"{target}, once this command has ended")
        else:
            shutil.rmtree(target)
            ui.ok("removed", str(target))
    src = data_dir() / "source"
    if src.exists() and not ctx.would(f"remove {src}"):
        src.unlink()
    ui.plain(ui.dim(f"kept: the state in {dirs.state()} and the plans in {data_dir() / 'plans'}"))
    return 0
