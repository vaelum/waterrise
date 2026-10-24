# Changelog

Versions are `<year>.<month>.<n>`: n counts the month's releases from 1, and
the month has no leading zero. butler and description pin waterrise by tag in
their `vendor/waterrise` submodule, and `waterrise install --check <pin>`
accepts an installed waterrise that is at least the pin, as long as the pin is
not older than `OLDEST_PIN` (the oldest one it still serves; a release that
breaks its callers raises it). Keep these sections accurate before tagging.

## [2026.10.5]

### Added

- **A protocol for every step.** `waterrise note <id> <step> TEXT` adds a dated
  note to an agent step's protocol, and `waterrise run <id> <step> -- COMMAND`
  runs a command with its output going to the terminal and the protocol as it
  comes, then its exit status and duration. Protocols live in
  `planning/protocols/<plan>/<step>.md` and are never committed (`init` and the
  first note add `protocols/` to the planning folder's `.gitignore`). A file
  stops growing at 5 MB, where a command keeps its last 50 lines; values from
  `~/.secrets` and credential-like environment variables are masked. The
  guide, the planning skill and the work-plan prompt tell agents to keep one;
  `check` warns about a done agent step without one, and errs on a credential
  in one. A plan's page shows each step's protocol at the step, live, and an
  active step's last note as "now".

### Changed

- **A plan's page shows its reports first**, under the summary, and every one
  of them: those whose `about` names the plan or one of its steps (shown at
  the step too), and those the plan names in `related` or `context`. A report
  whose sidecar doesn't parse is listed with its problem instead of
  disappearing, and one the plan names that doesn't exist is listed as
  missing. The open page follows new and changed reports live.
- `check` warns about a report whose `about` names a step its plan doesn't have.

## [2026.10.4]

### Added

- **waterrise runs on Windows.** `py -3 -m waterrise install` from a plain
  PowerShell makes the venv without symlinks, writes `waterrise.cmd` into
  `%LOCALAPPDATA%\waterrise\bin` and puts that folder on the user's PATH
  (`--no-path` leaves PATH alone). `--boot` starts the service at login from
  a value in the user's Run key, with `pythonw.exe` and no window; `--no-boot`
  and `--uninstall` remove it. The service starts detached with no console,
  and an install restarts an older one. Data and state default to
  `%LOCALAPPDATA%\waterrise\` and its `state\`; the XDG variables still
  override them.

### Fixed

- **Answers are no longer lost to concurrent writes on Windows.** The lock
  between processes was skipped where `fcntl` is missing; all three locks
  (the answers files, the service start, the central home) now go through
  `waterrise/lock.py`, with `msvcrt` on Windows, and a platform with neither
  is an error. Replacing a file retries while another process has it open.
- **Every file is read and written as UTF-8, and committed files with LF**,
  whatever the platform's code page; console output that the console can't
  encode becomes `?` instead of an error.
- An item's `#:schema` line has forward slashes on Windows too, and the
  work-plan server matches git's and Python's spelling of the same path;
  when it can't place a file it says what git said.
- The tests run in a home of their own (`HOME`, `USERPROFILE`, `LOCALAPPDATA`,
  `APPDATA`) and can't write into the real one; a test run on Windows did.
- **Found by running on Windows**: the work-plan server's `git` hung for good
  behind the server's own stdin, so every process waterrise starts now gets
  no stdin; its JSON-RPC replies went out in cp1252 before Python 3.15, and
  the CLI's output into a pipe is UTF-8 now too; `serve` waited in vain for a
  service whose pid was the venv launcher's; a refused request reset its
  connection; the service at login, under `pythonw.exe`, ended on its first
  line for want of a stdout; `service stop` waited for a `service.json` that
  could not be deleted while it read it; `projects home` deleted lock files it
  still held; `install --uninstall` through the launcher deleted the venv it
  ran from.

## [2026.10.3]

### Changed

- **The pages wear vaelum.dev's dark look.** Charter for titles and reading,
  Geist and Geist Mono for the controls (self-hosted under `static/`, OFL),
  waterrise's blue accent. The index opens on what waits on you; an item's
  to-do sits beside the article and wraps under it when narrow; the header
  shows where you are and a live pill. Dark is the default, `data-theme="light"`
  the light one. Behaviour and copy are unchanged.
- **The waiting list on the index scrolls in a box of its own** and takes a
  project filter when more than one project waits on you; the browser
  remembers the choice.
- **An item's kind and state sit in a bubble in the middle of the header**,
  in place of the folder in the trail (`butler / h-8xmhhz`, not
  `butler / reports/ / h-8xmhhz`) and of the badge line above the title. The
  state is the folder for a plan, decision or review, and `state` for a note
  or report.

### Added

- **Notes and reports have a state**: `state = "draft"` or `"finished"`
  (default `finished`, so the ones already written stay as they were).
  `waterrise new note` and `new report` start them as drafts. An older
  waterrise reports `state` in a note or report as an unknown key.

## [2026.10.2]

### Added

- **Two homes for a project's plans.** A project plans in its repository, as
  before, or in the central one, `$XDG_DATA_HOME/waterrise/plans/<slug>/planning/`,
  which waterrise makes on first use and commits after every change: every
  command that writes, `check`, and every click in the page. The home is in the
  registry only. `waterrise projects [list|add|remove|home]`; `projects home
  <slug> central|repo` carries the whole folder across, refused while the
  service is writing an answers file. An unregistered checkout without
  `planning/` is told how to register it.
- **Reports.** A fifth kind (`h-…`, in `planning/reports/`): a TOML sidecar
  beside one self-contained HTML document, attached to a plan (`about`) or to
  the project. `waterrise new report <name>` writes the pair from a starter;
  `check` wants the document, HTML, at most 16 MB, nothing loaded from another
  origin and nothing that looks like a credential. A plan's page lists its
  reports, the repository's page all of them, and a report's page shows its
  document in a frame whose scripts run with an opaque origin and no network.

### Fixed

- `install --boot` restarts the unit's service when it runs an older version,
  as a plain `install` does; with an unchanged unit it kept serving the old
  code. No restart when nothing answers yet.
- The work-plan server resolves a central project's plan by its file, not
  only by its id.

## [2026.10.1]

### Added

- **waterrise is butler's `planning` command as a tool of its own.** The code of
  butler v0.11.0's `src/butler/planning/` (typed folders, `check`, `status`,
  `gate`, the one service per user and its pages, the agent guide), moved with
  its history, and its command tree one level up: `waterrise check`,
  `waterrise status p-…`, `waterrise serve`, … Everything the folder holds is
  unchanged: format 1, ids, answers files, folders and the guide's rules.
- **`python -m waterrise install`** from a checkout: a venv of its own under
  `$XDG_DATA_HOME/waterrise/venv`, the checkout pip-installed into it, and
  `~/.local/bin/waterrise`. Stdlib only. The checkout is remembered, so after
  a `git pull` a bare `waterrise install` reinstalls it. `--check VERSION`
  says whether this waterrise serves a pin; `--uninstall` removes the
  launcher and the venv and keeps the registry and the plans.
- **`waterrise install --boot`**: a systemd user unit, `waterrise.service`,
  running `waterrise service run` at login; `--no-boot` removes it. An install
  restarts the unit when it runs an older version.
- **The work-plan MCP server and the `planning` and `work-plan` skills**, from
  description, now in `waterrise/mcp/` and `skills/`. The server runs the
  installed `waterrise status --json`, so every repository is read by one
  version, whatever its butler pins.

### Changed (from butler's `planning`)

- The service is named `waterrise`, its state is in `$XDG_STATE_HOME/waterrise/`
  (the registry in `registry.json`), `WATERRISE_PORT` moves it, and the page's
  write header is `X-Waterrise`. The first start imports butler's
  `$XDG_STATE_HOME/butler/planning/repos.json`, every project planning in its
  repository, and leaves the old file for an older butler.
- Messages name `$WATERRISE_PROG`, `waterrise` by default; butler sets it to
  `python butler.py planning`, so its output stays word for word.
- `waterrise guide` is the installed tool's guide; it no longer varies with a
  project's pin.
