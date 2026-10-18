# Changelog

Versions are `<year>.<month>.<n>`: n counts the month's releases from 1, and
the month has no leading zero. butler and description pin waterrise by tag in
their `vendor/waterrise` submodule, and `waterrise install --check <pin>`
accepts an installed waterrise that is at least the pin, as long as the pin is
not older than `OLDEST_PIN` (the oldest one it still serves; a release that
breaks its callers raises it). Keep these sections accurate before tagging.

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
