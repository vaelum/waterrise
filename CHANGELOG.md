# Changelog

Versions are `<year>.<month>.<n>`: n counts the month's releases from 1, and
the month has no leading zero. butler and description pin waterrise by tag in
their `vendor/waterrise` submodule, and `waterrise install --check <pin>`
accepts an installed waterrise that is at least the pin, as long as the pin is
not older than `OLDEST_PIN` (the oldest one it still serves; a release that
breaks its callers raises it). Keep these sections accurate before tagging.

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
