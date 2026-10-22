"""`waterrise install`: the venv, the launcher, --check and --uninstall, in a temp HOME."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from waterrise import __main__ as cli
from waterrise import OLDEST_PIN, __version__, install
from waterrise.errors import WaterriseError

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def home(tmp_path, fake_home, monkeypatch):
    fake_home(tmp_path)
    monkeypatch.delenv("WATERRISE_PROJECT", raising=False)
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    return tmp_path


@pytest.fixture
def fake_pip(home, monkeypatch):
    """The venv and pip steps, without the network: a venv whose `waterrise`
    prints a version, and a record of what was installed."""
    if os.name == "nt":
        pytest.skip("the POSIX venv and launcher, with /bin/sh scripts; Windows' are tested in test_platform.py")
    installed: list[Path] = []

    def create(path: Path) -> None:
        (path / "bin").mkdir(parents=True)
        (path / "bin" / "python").symlink_to(sys.executable)

    def pip(path: Path, checkout: Path) -> None:
        installed.append(checkout)
        script = path / "bin" / "waterrise"
        script.write_text(f"#!/bin/sh\necho 'waterrise {__version__}'\n", encoding="utf-8")
        script.chmod(0o755)

    monkeypatch.setattr(install, "create_venv", create)
    monkeypatch.setattr(install, "pip_install", pip)
    monkeypatch.setattr(install, "_venv_works", lambda path: (path / "bin" / "python").exists())
    return installed


def run(*argv: str) -> int:
    return cli.main(["--no-color", "install", *argv])


@pytest.mark.parametrize("installed, wanted, ok", [
    ("2026.10.1", "2026.10.1", True), ("2026.11.1", "2026.10.1", True),
    ("2027.1.3", "2026.10.1", True), ("2026.10.2", "2026.10.1", True),
    ("2026.10.1", "2026.10.2", False), ("2026.10.1", "2026.11.1", False),
    ("2026.10.2.dev0", "2026.10.1", True), ("2026.10.1.dev0", "2026.10.1", False),
    ("2026.10.1", "v2026.10.1", True),
])
def test_at_least_the_pin(installed, wanted, ok):
    assert install.satisfies(installed, wanted, oldest="2026.10.1") is ok


def test_a_pin_older_than_the_floor_is_refused_even_by_a_newer_waterrise():
    assert install.satisfies("2027.3.1", "2026.11.1", oldest="2027.1.1") is False
    assert "no longer serves pins before 2027.1.1" in install.refusal("2027.3.1", "2026.11.1", oldest="2027.1.1")
    assert install.satisfies("2027.3.1", "2027.1.1", oldest="2027.1.1") is True


@pytest.mark.parametrize("bad", ["1.0.0", "2026.09.1", "2026.10.0", "2026.13.1", "2026.10", "26.10.1"])
def test_only_calendar_versions_parse(bad):
    with pytest.raises(WaterriseError):
        install.parse(bad)


def test_this_version_and_the_floor_are_calendar_versions():
    assert install.parse(__version__) and install.parse(OLDEST_PIN)


def test_check_exit_codes_and_messages(home, capsys):
    assert run("--check", OLDEST_PIN) == (0 if install.parse(__version__) >= install.parse(OLDEST_PIN) else 1)
    year = install.parse(__version__)[0]
    assert run("--check", f"{year + 1}.1.1") == 1
    err = capsys.readouterr().err
    assert f"waterrise {__version__} is installed, and this needs {year + 1}.1.1 or later" in err
    assert "python3 -m waterrise install" in err
    assert run("--check", "2026.1.1") == 1
    assert "bump the pin" in capsys.readouterr().err


def test_install_links_the_launcher_and_remembers_the_checkout(fake_pip, home, capsys):
    assert run() == 0
    link = home / ".local" / "bin" / "waterrise"
    assert link.is_symlink() and Path(os.readlink(link)) == install.venv_script()
    assert fake_pip == [REPO]
    assert (install.data_dir() / "source").read_text(encoding="utf-8").strip() == str(REPO)
    out = subprocess.run([str(link), "--version"], capture_output=True, text=True, encoding="utf-8").stdout
    assert out.strip() == f"waterrise {__version__}"
    assert "not on PATH" in capsys.readouterr().out
    # A second install reuses the venv and the link.
    assert run() == 0
    assert fake_pip == [REPO, REPO]


def test_install_refuses_a_launcher_that_is_not_its_own(fake_pip, home, capsys):
    link = home / ".local" / "bin" / "waterrise"
    link.parent.mkdir(parents=True)
    link.write_text("someone else's\n", encoding="utf-8")
    assert run() == 1
    assert "is not waterrise's launcher" in capsys.readouterr().err
    assert link.read_text(encoding="utf-8") == "someone else's\n"


def test_uninstall_keeps_the_registry_and_the_plans(fake_pip, home):
    assert run() == 0
    state = home / ".local" / "state" / "waterrise"
    state.mkdir(parents=True)
    (state / "registry.json").write_text('{"repos": []}\n', encoding="utf-8")
    plans = install.data_dir() / "plans"
    plans.mkdir()
    assert run("--uninstall") == 0
    assert not (home / ".local" / "bin" / "waterrise").exists()
    assert not install.venv_dir().exists()
    assert (state / "registry.json").exists() and plans.is_dir()


def test_dry_run_changes_nothing(fake_pip, home):
    assert cli.main(["--no-color", "-n", "install"]) == 0
    assert not install.venv_dir().exists() and fake_pip == []


@pytest.mark.skipif(os.environ.get("WATERRISE_INSTALL_TEST") != "1",
                    reason="a real venv and pip install; CI sets WATERRISE_INSTALL_TEST=1")
def test_real_install_from_this_checkout(home):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("XDG_", "WATERRISE_"))}
    env["HOME"] = str(home)
    # --no-path: on Windows the real user PATH is in the registry, not in env.
    subprocess.run([sys.executable, "-m", "waterrise", "install", "--no-path"], cwd=REPO, env=env, check=True)
    link = install.launcher()
    out = subprocess.run([str(link), "--version"], capture_output=True, text=True, env=env, cwd=home, encoding="utf-8")
    assert out.stdout.strip() == f"waterrise {__version__}"
    rc = subprocess.run([str(link), "install", "--check", OLDEST_PIN], env=env, cwd=home).returncode
    assert rc == (0 if install.satisfies(__version__, OLDEST_PIN) else 1)
