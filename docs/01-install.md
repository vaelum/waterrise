# Install

waterrise needs Python 3.11 or newer and nothing else. It runs on Linux,
macOS and Windows.

## Linux and macOS

```
git clone https://github.com/vaelum/waterrise.git
cd waterrise
python -m waterrise install          # a venv of its own, `waterrise` on PATH
python -m waterrise install --boot   # and the service starting at login
```

`--boot` installs a systemd user service. macOS has no systemd, so there
leave `--boot` out; `waterrise serve` starts the service whenever it is not
running.

## Windows

The same commands, from a plain PowerShell. Nothing needs an administrator:

```
git clone https://github.com/vaelum/waterrise.git
cd waterrise
py -3 -m waterrise install --boot
```

`install` writes `waterrise.cmd` into `%LOCALAPPDATA%\waterrise\bin` and adds
that folder to your user PATH. Terminals opened afterwards see it
(`--no-path` leaves PATH alone). `--boot` starts the service at login with
the venv's `pythonw.exe`, so no window opens. Nothing restarts it if it
stops; `waterrise serve` starts it again.

## Update and remove

The install is not editable. After a `git pull` in the checkout, run
`waterrise install`: it remembers the checkout it was installed from.
`waterrise --version` says which version is installed.

`install --no-boot` stops the service from starting at login, and
`install --uninstall` removes the start at login, the launcher and the venv
(on Windows, also its PATH entry). It keeps the service's state and the
central plans; plans in your repositories are never touched.

## Where things live

|                 | Linux and macOS                      | Windows                              |
|-----------------|--------------------------------------|--------------------------------------|
| venv, central plans | `$XDG_DATA_HOME/waterrise/` (`~/.local/share/waterrise/`) | `%LOCALAPPDATA%\waterrise\` |
| service state, registry, log | `$XDG_STATE_HOME/waterrise/` (`~/.local/state/waterrise/`) | `%LOCALAPPDATA%\waterrise\state\` |
| launcher        | `~/.local/bin/waterrise` (a symlink) | `%LOCALAPPDATA%\waterrise\bin\waterrise.cmd` |
| start at login  | `~/.config/systemd/user/waterrise.service` (systemd; not on macOS) | the `waterrise` value in the user's Run key |

`XDG_DATA_HOME` and `XDG_STATE_HOME` override the defaults on every platform.
A project's plans are in its own repository's `planning/`, or, for a central
project, in the central plans above (see
[The pages](03-the-pages.md#two-homes)).
