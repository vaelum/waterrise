# waterrise

Typed plans, decisions, reviews and notes in a project's `planning/` folder,
a check for them, and one local service per user whose pages are where the
decisions get answered and the gated steps approved.

waterrise was the `planning` command of the butler harness, and it still is
one: in a project on the harness, `python butler.py planning …` runs the
installed `waterrise` with the same arguments.

## Install

```
git clone https://github.com/vaelum/waterrise.git
cd waterrise
python -m waterrise install          # a venv of its own, `waterrise` on PATH
python -m waterrise install --boot   # and the service starting at login
```

Python 3.11 or newer and nothing else, on Linux, macOS or Windows.

### On Windows

The same commands, from a plain PowerShell; nothing needs an administrator:

```
git clone https://github.com/vaelum/waterrise.git
cd waterrise
py -3 -m waterrise install --boot
```

`install` writes `waterrise.cmd` into `%LOCALAPPDATA%\waterrise\bin` and adds
that folder to your user PATH, which terminals opened afterwards see
(`--no-path` leaves PATH alone). `--boot` adds a `waterrise` value under
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run` that starts the
service at login with the venv's `pythonw.exe`, so no window opens. Nothing
restarts it if it dies; `waterrise serve` starts it again. `install --no-boot`
removes the value, and `install --uninstall` the value, the launcher, its PATH
entry and the venv.

### Where things live

|                 | Linux and macOS                      | Windows                              |
|-----------------|--------------------------------------|--------------------------------------|
| venv, central plans | `$XDG_DATA_HOME/waterrise/` (`~/.local/share/waterrise/`) | `%LOCALAPPDATA%\waterrise\` |
| service state, registry, log | `$XDG_STATE_HOME/waterrise/` (`~/.local/state/waterrise/`) | `%LOCALAPPDATA%\waterrise\state\` |
| launcher        | `~/.local/bin/waterrise` (a symlink) | `%LOCALAPPDATA%\waterrise\bin\waterrise.cmd` |
| start at login  | `~/.config/systemd/user/waterrise.service` (systemd; not on macOS) | the `waterrise` value in the user's Run key |

`XDG_DATA_HOME` and `XDG_STATE_HOME` override the defaults on every platform.
A project's plans are in its own repository's `planning/`, or for a central
project in the central plans above.

## Use

```
waterrise guide        # the rules and the format, for agents and people
waterrise init         # make planning/ a typed folder
waterrise new plan x   # an item with a fresh id
waterrise check        # validate the folder
waterrise status       # what waits on whom
waterrise serve        # the pages
```

`waterrise --help` lists the rest.

## Releasing

`python3 butler.py release <version>` (see `butler/butler.toml`). CI runs the
suite on Linux only, so before each release it runs on Windows too, in the
test VM on amos-pc (`tools/windows-vm/`, kept out of this mirror):

```
python3 butler.py windows-test --e2e
```

That is the suite under Python 3.13 and the newest Python 3, as a standard
user, failing if anything appeared in the real profile; then `install --boot`,
a reboot, the service answering after logon, and `install --uninstall`. On a
Windows machine of your own, the suite alone is
`py -3 -m venv .venv; .venv\Scripts\pip install -e ".[dev]"; .venv\Scripts\python -m pytest -q`.
