"""`waterrise install`: a venv of its own, and a launcher on PATH.

Arch marks the system Python externally managed, so `pip install --user` is
refused; waterrise does what butler's shim does and owns a venv. Stdlib only
(venv, ensurepip, pip), so `python -m waterrise install` runs in a fresh
checkout on a bare interpreter:

    $XDG_DATA_HOME/waterrise/venv      the venv, `pip install <checkout>` into it
    $XDG_DATA_HOME/waterrise/source    the checkout it was installed from
    ~/.local/bin/waterrise             a symlink to the venv's script

The install is not editable: a `git pull` takes effect on the next `install`,
which then needs no argument, because the checkout is remembered.

`--boot` adds a systemd user unit that runs the service at login (boot.py);
`--no-boot` removes it.

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

from . import OLDEST_PIN, __version__, boot, ui
from .context import Project
from .errors import WaterriseError

PACKAGE = Path(__file__).resolve().parent
CLONE_URL = "https://github.com/vaelum/waterrise.git"


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "waterrise"


def venv_dir() -> Path:
    return data_dir() / "venv"


def launcher() -> Path:
    return Path.home() / ".local" / "bin" / "waterrise"


def venv_script() -> Path:
    return venv_dir() / "bin" / "waterrise"


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
        text = (path / "pyproject.toml").read_text()
    except OSError:
        return False
    return re.search(r'^name\s*=\s*"waterrise"', text, re.M) is not None and \
        (path / "waterrise" / "__init__.py").is_file()


def recorded_source() -> Path | None:
    try:
        path = Path((data_dir() / "source").read_text().strip())
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
    py = path / "bin" / "python"
    if not py.exists():
        return False
    try:
        return subprocess.run([str(py), "-c", "import pip"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def create_venv(path: Path) -> None:
    # clear=True: a venv whose interpreter went away with a Python upgrade is
    # replaced, not patched.
    venv.EnvBuilder(with_pip=True, clear=True, symlinks=True).create(path)


def pip_install(path: Path, checkout: Path) -> None:
    # --force-reinstall: the version does not change between two commits, and
    # pip would otherwise call the old build satisfied. No dependencies to
    # reinstall with it.
    rc = subprocess.run([str(path / "bin" / "python"), "-m", "pip", "install", "--quiet",
                         "--disable-pip-version-check", "--force-reinstall", "--no-deps",
                         str(checkout)]).returncode
    if rc != 0:
        raise WaterriseError(f"pip could not install {checkout}", hint="its output is above")


def installed_version() -> str | None:
    script = venv_script()
    if not script.exists():
        return None
    try:
        out = subprocess.run([str(script), "--version"], capture_output=True, text=True, timeout=30)
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


def _link_launcher(ctx: Project) -> None:
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
    (data_dir() / "source").write_text(f"{checkout}\n")
    _link_launcher(ctx)
    version = installed_version()
    if version is None:
        raise WaterriseError(f"{venv_script()} does not run after the install")
    ui.ok(f"waterrise {version}", f"installed from {checkout} into {target}")
    if not _on_path(launcher().parent):
        ui.warn("note:", f"{launcher().parent} is not on PATH; add it, or run {launcher()}")
    if args.boot:
        boot.enable(ctx, venv_script())
    else:
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


def uninstall(ctx: Project) -> int:
    """The unit, the launcher and the venv. The registry and the plans stay."""
    boot.disable(ctx)
    link = launcher()
    if link.is_symlink() and Path(os.readlink(link)) == venv_script():
        if not ctx.would(f"remove {link}"):
            link.unlink()
            ui.ok("removed", str(link))
    target = venv_dir()
    if target.exists() and not ctx.would(f"remove {target}"):
        shutil.rmtree(target)
        ui.ok("removed", str(target))
    src = data_dir() / "source"
    if src.exists() and not ctx.would(f"remove {src}"):
        src.unlink()
    ui.plain(ui.dim(f"kept: the state in $XDG_STATE_HOME/waterrise and the plans in {data_dir()}"))
    return 0
