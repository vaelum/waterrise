# Changelog

Versions are `<year>.<month>.<n>`: n counts the month's releases from 1, and
the month has no leading zero. butler and description pin waterrise by tag in
their `vendor/waterrise` submodule, and `waterrise install --check <pin>`
accepts an installed waterrise that is at least the pin, as long as the pin is
not older than `OLDEST_PIN` (the oldest one it still serves; a release that
breaks its callers raises it). Keep these sections accurate before tagging.

## [2026.11.3]

### Added

- **`needs` are links.** A step's `needs`, an item's `needs`, `context` and
  `related`, and "waits on" in the to-do jump to the step, phase or decision they
  name (it opens and flashes; Back returns to where one was), or open the other
  item (`p-…/s2-1` at that step). One that is done is struck through.

### Fixed

- **A dropped step holds nothing up.** A step that needs a dropped one was
  never satisfied, so a done step after it failed `check` ("what it needs is
  not: …"). Taking the dropped step out of its `needs` made a gated step's
  approval stale, which the page then offered no way to answer. A dropped step
  now counts as finished, as it already did inside a phase.
- **A done gated step edited after its approval can be approved again.**
  `check` asks for a current approval on a done gated step, but the page showed
  no controls once a step was done. Now such a step shows **Review changes**,
  the review popup takes it, and `status` lists it as waiting on the user.
- **A gated step done without any approval can be approved afterwards.**
  `check` fails on it ("has no current approval: … has not answered"), but the
  page showed no controls on a done step that was never answered, so nothing
  could clear the error. Such a step now says it was done without approval and
  shows Approve and Disapprove, and `status` lists it as waiting on the user.
- **A plan's page stays quick while an agent's run streams output.** Every line
  `waterrise run` writes made the page fetch and redraw the whole plan every half
  second, several requests at once (a big plan: 0.8–1.9 s each, the browser busy
  79% of the time). Now the stream says a protocol only `grew` apart from a plan
  that changed; a page reloads one request at a time, at most once a second, and
  every 4 s for growth alone; an answer that did not change is not redrawn, and a
  late answer never overwrites a newer one. The service keeps its folder read and
  check while only protocols grow (it asks what the protocols say fresh, so a
  credential still shows at once) and keeps protocol summaries by size and mtime:
  the same plan's page now answers in ~30 ms, not 0.3–2.5 s.

- **A page holds still when its plan changes.** A re-render (the agent edits the
  plan, a save) no longer closes the folds one has open or opens the ones one
  closed: the Progress outline and its phases, design sections, a finding's
  evidence. Their defaults count once; only a phase that starts opens itself. The
  page, the panel beside the plan, the chat and an open protocol keep their scroll
  position, and an open protocol no longer collapses to "loading…" on the way.

## [2026.11.2]

### Added

- **A project chooses its worktree folder.** `worktrees = ".worktrees"` in
  `planning.toml`, relative to the main checkout and allowed to go up
  (`../proj-wt`); `worktrees/` stays the default. A machine can override it with
  `waterrise projects worktrees [SLUG|PATH] DIR`, and `--default` drops that.
  Everything that named `worktrees/` follows it: `worktree add`, `list`, `remove`,
  `assign`, where a plan lives, the ignore rule (written only for a folder inside
  the checkout), `check`, the page and the MCP tools. `worktree list`, `status` and
  the page say which folder is used and where it is set.
- **`tools/bench.py`** measures the commands and the page on a large synthetic
  project, and counts the git processes each starts.
- **Pause and Continue on a plan's page.** Paused, `status` says so and the MCP
  tools say PAUSED: the agent finishes the step it is on and waits;
  `wait_for_plan` returns CONTINUED when the plan is continued.
- **Progress beside a plan:** each phase with done/total and a bar, its steps
  with their state, each a link. At phone width the side panel comes first.
- **A plan's timeline:** its `[[log]]` and every step's protocol notes, newest
  first. The step being worked on has its protocol open, following its newest
  line.
- **A chat beside every plan**, built on its whole-plan comments, and
  `waterrise reply` for the agent's answers. A comment that has a reply waits on
  the user rather than the agent.
- **`/work-plan` can use several agents:** independent ready steps without a
  gate go to subagents; the agent you started stays the only writer of the plan.

### Changed

- **Much faster with many worktrees.** A plan's copies in worktrees that are
  byte-identical are parsed once: on a project with 100 plans and 40 worktrees,
  `status` went from 18 s to under a second.
- **`check` starts two git processes for HEAD, not one per item,** and finds the
  line of a problem from one index per item. It also finds committed plans whose
  names git quotes (non-ASCII), which it missed before.
- **Worktree state** takes the main checkout's HEAD from `git worktree list` and
  asks the worktrees a few at a time. A worktree whose git does not answer in time
  shows as unknown; it showed as clean on the page and dirty in `worktree list`.
- **The service keeps a page's folder read, its check and protocol scans in
  memory** while nothing behind them changes, and asks git afresh on every request.
  Its change streams walk the planning folders once per tick with `scandir`, shared
  by every open stream. An index page's own stream no longer reacts to protocols
  growing.
- The service's API level is 2 (`PUT /api/repos/<slug>/worktrees`); a newer
  command replaces an older running service, as before.

## [2026.11.1]

### Added

- **A plan tracked in the repository can live in a worktree,** on its branch, with
  its answers. A plan assigned to a worktree with a copy lives there, and an item
  made there, or only there, too. One rule says which copy counts, so commands, the
  page and `gate` read the same one from anywhere and an approval counts everywhere.
  Merging brings it back. Protocols stay in the main checkout. A central project's
  plans stay central.
- **Plans can be assigned to worktrees.** `waterrise assign ID NAME` or a picker
  on the plan's page. `status`, `show`, `list --worktree`, the page and the MCP
  tools say where a plan belongs, with the path. `run` and `note` refuse in another
  worktree and warn in the main checkout; `gate`'s exit status does not depend on
  location. `check` reports a plan whose worktree is gone, and it is never
  silently unassigned. The assignment is kept in `planning/assignments.json`.
- **`waterrise worktree add`, `list` and `remove`.** Worktrees go in a `worktrees/`
  folder at the top of the main checkout, which git ignores: the top `.gitignore`,
  or `.git/info/exclude` for a central project. `remove` also refuses a worktree
  whose unmerged branch has the only current copy of a plan.
- The repository's page lists worktrees and their state, and cards can be filtered
  by worktree.

### Changed

- **All worktrees of a repository are one project,** however a command is started.
  If you relied on a worktree's own `planning/`, a plan there that also exists in the
  main checkout, and is not assigned to that worktree, is no longer read from there;
  `check` run in that worktree names it. A worktree outside `worktrees/` holds no
  plans of its own any more: merging or moving them brings them back.
- The registry has one entry per repository. When the service starts, an entry for
  a linked worktree is dropped if its main checkout is registered, and otherwise
  becomes the main checkout's. An entry whose plans are in its own central home is
  kept. Nothing on disk changes.
- `waterrise init` now adds `/worktrees/` to the top `.gitignore`.

- **A change since an answer is reviewed in a popup.** The plan's page keeps one line
  and a **Review changes** button; the popup shows each change once, by phase, step,
  field and word, inline or side by side, with what did not change folded. No JSON.
  While an approval is stale, Approve and Disapprove are only in the popup.
- **An approval on outdated text is refused.** `PUT .../approval/...` takes an optional
  `sha256` of the text the user was shown, and answers 409 if it is no longer the
  current one. The pages always send it. What an approval covers, and what
  `waterrise gate` accepts, have not changed.

### Fixed

- A card can be dragged from a full lane to a short one: all lanes are as tall as
  the fullest, and all of a lane takes a drop.

## [2026.10.7]

### Changed

- **waterrise is now under the GNU Affero General Public License, version 3
  only (AGPL-3.0-only)**, no longer MIT. You may use, change and share it
  under the AGPL's terms; if you run a changed waterrise for other people
  over a network, you must offer them its source. The earlier releases have
  been withdrawn, and this is the only one. The bundled marked.js (MIT) and
  Geist fonts (SIL OFL) keep their own licences.
- **Agents are told about "the user".** The planning and work-plan skills,
  the work-plan prompt, and the MCP tools' titles and descriptions no longer
  name their author. The status lines follow: `AMOS ACTED` is now
  `USER ACTED`, and "waiting on amos" is "waiting on the user". An agent
  loop that matches on the old words needs the new ones.
- The README and the MCP server's plan-reference example name no machine or
  repository of the author's.

## [2026.10.6]

Only the documentation changes in this release; nothing to update but the
version.

### Added

- **User documentation in `docs/`.** Installing waterrise, the planning
  folder, the pages, working with an agent (including the skills and the
  work-plan MCP server, set up by hand), and the commands, each on a page of
  its own. The guide stays where `waterrise guide` reads it, in the package,
  and the docs point at it.

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
