# Planning with waterrise: the guide for agents

This is the rulebook for `planning/` in any project that plans with
waterrise. It is printed by `waterrise guide` and ships with the installed
tool, so it matches the waterrise that reads and checks the folder, whichever
project you are in. Read it before writing or changing anything in a planning
folder. The reference tables at the end are generated from waterrise's own
type definitions; nothing else is authoritative.

In a project on the butler harness, `python butler.py planning …` is the same
command: it runs the installed `waterrise` with the same arguments, and its
messages name `python butler.py planning` where they would name `waterrise`.

## 1. The folder

```
planning/
  planning.toml   format = 1, user, plan_approval; its presence makes the folder typed
  draft/          items waiting on the user
  todo/           approved by the user; decisions may still be open
  in-progress/    being worked on
  done/           finished, kept as the record
  notes/          context several items share; `state` in the file: draft or finished
  reports/        HTML reports and their sidecars (`<name>.toml` + `<name>.html`); `state` as notes
  answers/        <id>.json: the user's answers and approvals — never yours to write
  assignments.json which worktree each plan belongs to (`waterrise assign`) — never yours to write
  protocols/      <plan id>/<step>.md: what was noted and run for each step; never committed
  .schema/        the JSON Schema editors validate against (`waterrise schema`)
  .gitignore      keeps the answers' lock files and protocols/ out of git (`waterrise init` writes it)
```

- **The folder is the state.** Move an item with
  `waterrise move <id> <folder>`, which checks the target
  folder's rules first and then runs `git mv`.
- **Each item is one TOML file** with a `kind`: `plan`, `decision`, `review`,
  `note` or `report`. The file name is a readable slug and may change.
- **The `id` is the identity** and never changes: `p-3p758x` is the kind's
  letter and six base32 characters. `waterrise new` makes it; never invent,
  edit or reuse one. The answers file, the page's URL and every reference
  hang on it, which is why a move or a rename breaks nothing.
- **Two writers, never the same file.** You write the item. The planning
  service writes `answers/<id>.json` when the user clicks in the page. The only
  commands of yours that touch an answers file are `waterrise resolve`,
  `waterrise reply`, `waterrise convert` and `waterrise assign`, which writes
  `assignments.json`.
- Markdown plans and the old HTML forms are shown read-only and reported by
  `check` as warnings. `waterrise convert <file>` makes a typed skeleton.

## 2. Which kind

| you have | write a |
|---|---|
| work with steps, or questions that lead to work | `plan` |
| one question for the user that no plan owns, or a settled choice that must not be reopened (`final = true`) | `decision` |
| findings of a review or an audit, to be triaged and handed to plans | `review` |
| context several items read first: ground rules, a data model, research | `note` |
| what you measured, compared or investigated, with numbers, tables or charts | `report` |

`waterrise new plan <name>` (or `decision`, `review`, `note`, `report`)
writes the item from its template with a fresh id. Fill it in, then run
`waterrise check`.

**Reports.** When the findings are numbers, a chart, a table to sort, or
anything longer than a `[[section]]` should be, write a report instead of
prose in a plan. `waterrise new report <name>` makes the pair in `reports/`:
the sidecar (`h-…`, its title and summary, and `about = "p-…"` for the plan it
reports on, `about = "p-…/s2-1"` for one of its steps, or nothing for one about
the project) and a starter document that passes `check`. A plan's page lists
its reports first, under its summary: every report whose `about` names the plan
or one of its steps (that one also at the step), and every report the plan
names in its `related` or `context`. A report whose sidecar can't be read is
shown there with its problem, never left out. The document is one HTML file with everything inline: its
CSS, its script, its data and its images (inline SVG or `data:` URLs). The
page shows it in a sandboxed frame that loads nothing remote, so a CDN script,
a web font or a remote image would not load, and `check` refuses them; a
plain link is fine. Keep to the light and dark tokens of the starter, label
every axis, and put the answer before the numbers. Never a credential in a
report: `check` looks for them. A report is read-only in the page; the user
comments on it through its sidecar like on any item.

## 3. Writing TOML that stays valid

- Put every top-level key (`why`, `acceptance`, `needs`, …) **before** the
  first `[table]` or `[[array]]` header; after a header, keys belong to it.
- `[[phase.step]]` attaches to the `[[phase]]` above it, and
  `[[decision.option]]` to the `[[decision]]` above it. Indent them by two
  spaces so the nesting shows.
- **Prose** fields (`why`, `context`, `details`, `body`, `subject`, `method`,
  `evidence`) hold Markdown. Write them as multi-line literal strings between
  three single quotes on each side: nothing inside needs escaping, but the text
  itself must not contain three single quotes in a row.
- Dates are bare TOML dates (`created = 2026-09-30`), not strings.
- Unknown keys are errors. `check` names the key and the line.
- Never a credential value in an item. Name the variable or the file.

## 4. Writing decisions

A decision is a question only the user can answer. Everything the user needs
to answer it is in the decision itself.

- One question each, ending in a question mark.
- `context`: the facts, what each choice costs, and where they come from, as
  checked on `as_of`. The user answers from this, not from a chat.
- Two to four options. Each stands on its own and says what picking it
  means (`hint`). No straw men, no "other" (the user can always write an own
  answer unless `own_answer = false`). Only a decision that is already
  resolved may keep more, as the old forms' decisions do once converted.
- One `recommend`, and a `because` that gives the reason in one sentence.
  With `multiple = true`, `recommend` may be a list.
- Ask decisions through the page, not in chat: after writing them run
  `waterrise serve` and give the user the URL it prints.
- **Acting on an answer.** `waterrise status` (or `waterrise list decisions`)
  shows what the user picked or wrote. When you have changed the plan to
  follow it, record it in the decision:

  ```toml
  resolved = ["random"]          # option ids, or ["own"]
  outcome = "amos, 2026-09-30: as recommended. <what changed because of it>"
  resolved_on = 2026-09-30
  ```

  A resolved decision is locked in the page. Never delete a decision, and
  never resolve one the user has not answered.

## 5. Writing phases and steps

After the decisions and the scope are settled, a plan gets fixed phases of
steps. The user approves the plan as a whole before it leaves `draft/` (when
`plan_approval = true`).

- **A step is one checkable outcome**, owned by one side: `by = "agent"` or
  `by = "user"`. Split a step that both sides act on.
- A user step says exactly what to do, where, and what to report back.
- `done_when` says how to tell it is done; `needs` names the decisions, steps
  or phases that come first (`needs = ["alerts", "s1-1", "p1"]`, or
  `p-4k7x2m/s2-1` in another item). A dropped step counts as finished:
  leave it in the `needs` of the steps after it.
- States: `open`, `active`, `done`, `blocked`, `dropped`. `blocked` and
  `dropped` need a `reason`. `done` takes the `commit`.
- **Gates.** A step gets `gate = true` when it is hard to undo or visible
  outside the machine: a deploy, a push to a main branch, a release or tag,
  deleting data, repositories or accounts, rotating or revoking credentials,
  sending a message or an email, spending money, changing a live host's
  configuration. When unsure, gate it.

## 6. The four modes

| mode | when | the user, in the page | you |
|---|---|---|---|
| **Decide** | a draft with open decisions | picks an option or writes an own answer | write the decisions (section 4), run `serve`, give the URL; act on answers, record them |
| **Extend** | any time | comments on the item, a section, a phase, a decision, a step or a finding; on a plan, writes in the chat beside it | read comments in `status`, change the item, then `waterrise resolve <id> <c…> --note "what you did"`; answer a chat message (a comment on the whole plan) first with `waterrise reply <id> <c…> TEXT`; when something new comes up mid-work, add a decision and put it in the `needs` of the steps it holds up |
| **Track** | todo and in-progress | watches progress live; ticks their own steps; pauses and continues the plan | set `state = "active"` when you start a step and `"done"` with `commit` when it is, append a `[[log]]` entry, `waterrise move` the item, `waterrise check` after every edit; while `status` says the plan is paused, finish the step you are on and start nothing new |
| **Gate** | before a gated step; before a plan leaves draft | approves or disapproves, with a note; a change since an earlier answer is reviewed and answered in a popup | run `waterrise gate` right before the action (section 7) |

A user step the user ticked in the page shows as "done in the page, not yet in
the file" until you set its `state = "done"`.

**Pause.** The user can pause a plan in its page. `status` then says so, and the
MCP tools say PAUSED: finish the step you are on (commit it and record it done),
start nothing new, and wait until the plan is continued. Answers, approvals and
comments still arrive while it is paused.

**The chat.** A plan's page has a chat beside it: each message is a comment on the
whole plan. Answer every one with `waterrise reply` (what you will do, or a question
back), make the change, and `waterrise resolve` it with what changed. A comment you
replied to waits on the user, who writes again to go on.

**The order is the user's.** The repository's page shows each folder as a
lane, and the user drags the items into the order they want them done. The
service keeps it in the items' answers files (`order`), and `waterrise list`,
`status` and the page all show items in that order. Take work from `todo/`
top-down. Moving an item to another folder starts it at the end of its new
lane. The user can also drag a card to another lane: that is `waterrise move`,
with the same folder rules, and it moves the file (with `git mv` when git
tracks it). So an item's folder can change under you; read it again rather
than assume where it is. A card also shows what its `needs` names and what needs it, so a plan's
`needs` should list every plan that has to be done first.

The top of every item's page is the user's to-do list, built from the files:
the decisions to answer, the gates to approve, and the user's own steps whose
`needs` are all finished. It is only as good as the `needs` you write: a user
step without them shows as ready at once, and one that waits on a step you
forgot to set to `done` never shows. `waterrise status` prints the same list.

## 7. Approvals

- **Right before** a gated step's action, run
  `waterrise gate <id> <step>` and quote its line. Exit 0
  means approved by the user in the page, and the step unchanged since. That
  line is what an action evaluator (a permission classifier, a hook, a person
  reading the transcript) can check.
- Exit 1 means not answered, disapproved (with the user's note), or stale.
  **Stop** and tell the user; give them the URL from `waterrise serve`. Never
  work around a closed gate.
- When the text changed after the user's answer, the page shows one line and
  **Review changes**, and the user reads the change and answers again in a popup,
  by phase, step and word. Each stale approval is answered on its own; there is no
  Approve all. It never opens by itself. An answer given on text that has changed
  since it was drawn is refused, so the user sees your edit before approving it.
- An approval covers the step's text: every field but `state`, `commit` and
  `reason`. Editing a gated step after its approval makes the approval stale.
  Record progress on it in the `[[log]]`, not in the step. An edit to a gated
  step that is already done asks again too: the user reviews it in the page.
- A disapproved step is dropped (`state = "dropped"`, `reason` = the user's
  note) or rewritten, which asks the user again.
- `waterrise gate <id>` without a step checks the plan's own approval, which
  `waterrise move <id> todo` also requires. That approval is all todo/ asks
  for: a plan may wait there with decisions still open. in-progress/ wants
  every open decision held by the `needs` of a step, so no work starts past an
  unanswered question. Without `plan_approval`, todo/ wants every decision
  resolved instead.
- **Never** write anything under `planning/answers/`, and never call the
  planning service's API yourself (`curl`, scripts). An approval you wrote is
  worthless, and it shows in the transcript and in git.

## 8. Keeping an item current

During implementation, after each step:

1. set the step's `state` (and `commit`);
2. append a `[[log]]` entry (`on`, `text`, `step`, `commit`);
3. set the item's `updated` to today;
4. run `waterrise check` and fix what it says;
5. when a phase's work changes the plan, say so in a decision or a comment
   answer, not silently.

`waterrise status` is where to look first when you pick an item up: it lists
what waits on the user and what waits on you.

**Every agent step keeps a protocol** in `protocols/<plan id>/<step>.md`,
which the user reads at the step in the page, live, while you work:

- before the step's first command, `waterrise note <id> <step> TEXT`: what
  you are about to do, and why;
- every command whose output matters (tests, builds, installs, checks,
  anything that can fail) through `waterrise run <id> <step> -- COMMAND ...`:
  its output goes to your terminal and to the protocol as it comes, with its
  exit status; never hand-build a redirect into the file;
- a note whenever what you saw changes what you do next, and a closing note
  when the step is done.

The protocol is the detail; the `[[log]]` entry stays the summary. Values in
`~/.secrets` are masked before anything is written, but don't run commands
that print credentials. On Windows, `run` starts the command as given: a shell
built-in needs `-- cmd /c ...` or `-- powershell -Command ...`.

## 9. Worktrees

All worktrees of a repository are **one project with one page**, rooted at the
main checkout. Every command, run in any worktree, reaches that project, whether
it was started directly, through butler or by the MCP server. A plan tracked in
the repository can live in a worktree, on its branch. A separate clone is a
separate project. A bare repository has no main checkout, so each of its worktrees
stays its own project. A central project's plans stay in the central repository,
and all its worktrees read them there.

- **The project's worktree folder:** worktrees go in one folder, `worktrees/` at
  the top of the main checkout unless the project sets another: `worktrees =
  ".worktrees"` in `planning.toml`, a path relative to the main checkout, `/`
  separated, which may go up (`../proj-wt`); never absolute or `~`. A machine can
  override it for itself with `waterrise projects worktrees [SLUG|PATH] DIR`, and
  `--default` drops that again; without DIR it says which folder is used and where
  it is set. `worktree list`, `status` in a worktree and the page say the same.
  Wherever this guide says `worktrees/`, it means that folder.
- **Adding one:** `waterrise worktree add NAME [--branch B] [--from REF] [--plan ID]`
  makes one in the folder, on a branch named NAME (the existing one if there is
  one, otherwise new from the main checkout's HEAD), and has git ignore a folder
  inside the checkout: in the top `.gitignore`, left uncommitted and said so, or in
  `.git/info/exclude` for a central project. A folder beside the checkout needs no
  rule.
- **Changing the folder** moves nothing. A plan assigned to a worktree that is not
  in the new folder reads as gone, and `check` says so and names the setting; bring
  the worktree over with `git worktree move <old> <new>` (make the new folder
  first), or set the folder back.
- **Where a plan lives.** Every worktree checks out a copy of every plan, and one
  rule says which copy counts, wherever a command runs: a plan assigned to a
  worktree that has a copy lives there; otherwise a plan in the main checkout lives
  there; and an item that exists only in a worktree under `worktrees/` lives there,
  in the first by name if in several. `show` says which. `waterrise new` run in such
  a worktree makes the item there.
- **Its file and its answers are together, in its home.** Edit the plan there and
  commit both there, with the code: `git -C <its checkout> commit -- <files>`. The
  page writes answers there too, uncommitted like any other answers, and `gate`
  reads them there from anywhere. `plan_status` and `show` name the checkout. Try
  again if git's index is locked.
- **Its protocols stay in the main checkout,** wherever it lives. They are not
  committed, and `git worktree remove` would delete them from a worktree.
- **Coming back is a merge.** Once the branch is merged, `waterrise assign ID --none`
  makes the main checkout's copy count again. A plan that was only on the branch
  counts from the main checkout as soon as the merge brings it there. `assign` warns
  when another copy starts to count that differs, because answers given in the old
  one do not show until they are committed and merged: so when assigning a plan
  that has uncommitted answers, commit them first, and merge them into the branch.
  A conflict in an answers file is resolved like any other, keeping both sides'
  entries.
- **A copy of a plan that lives elsewhere** is read by nobody. `check`, run in a
  worktree, warns about edits to it and names the file to edit. Editing a plan in
  its home is normal and says nothing.
- **Assigning:** a plan belongs to one worktree or to none, and a worktree can
  hold several plans. `waterrise assign ID NAME` and `waterrise assign ID --none`
  change it, as the user can in the page. NAME is `main` for the main checkout,
  or a folder under `worktrees/`. It is kept in `planning/assignments.json` in the
  main checkout, which branches do not change, so it never makes a merge conflict.
  Commit it there like answers, or a clone will not have it;
  like `answers/`, it is never yours to edit by hand. `status`, `show` and the MCP
  tools give the name and the path; `list items --worktree NAME` filters by it. An
  assignment does not touch an approval.
- **Where you work on an assigned plan:** `run` and `note` go on in the assigned
  worktree, warn and go on in the main checkout, which has legitimate closing work
  such as merging and releasing, and stop in any other worktree, naming the right
  path. `gate`'s exit status does not depend on where it runs; it only adds a line
  saying so. Reading commands never refuse.
- **A worktree that is gone** is never silently unassigned. Its plan is read from
  the main checkout again. `check` warns while the plan is not in `done/`, the page
  and `status` say so, and nothing is stopped because of it. In `done/` it is normal,
  and the record stays.
- **Worktrees elsewhere,** made by other tools, resolve to the project as well,
  and commands work from them. Plans cannot live in them or be assigned to them, and
  only `worktree list --all` shows them. `check` never warns about where they are,
  only about edits to their copy when it runs inside one.
- `waterrise worktree remove NAME` runs `git worktree remove`: it refuses a dirty
  worktree, one holding a plan in `todo/` or `in-progress/`, and one whose branch,
  not merged, has the only current copy of a plan. It never forces, and the branch
  stays.

## 10. Commands

| command | does |
|---|---|
| `waterrise guide` | this text |
| `waterrise init` | make `planning/` typed |
| `waterrise new KIND NAME` | a new item with a fresh id |
| `waterrise check [--strict] [--json]` | validate the whole folder; exit 1 on an error |
| `waterrise status [ID] [--json]` | what waits on the user and on you |
| `waterrise list [items\|decisions\|steps\|approvals\|comments\|findings] [--all] [--by agent\|user] [--everywhere] [--worktree NAME] [--json]` | one line each; open ones unless `--all` |
| `waterrise show ID` | an item as text |
| `waterrise move ID FOLDER` | move it, after checking the folder's rules |
| `waterrise gate ID [STEP]` | exit 0 only if approved and unchanged |
| `waterrise resolve ID COMMENT --note TEXT` | mark a comment handled |
| `waterrise reply ID COMMENT TEXT` | answer a comment or a chat message, without marking it handled |
| `waterrise assign ID NAME\|--none` | assign a plan to a worktree, or to none |
| `waterrise worktree add NAME [--branch B] [--from REF] [--plan ID]` | a worktree in `worktrees/` |
| `waterrise worktree list [--all] [--json]` | name, branch, clean or not, ahead/behind, path and plans |
| `waterrise worktree remove NAME` | `git worktree remove`; refused if dirty, holding an open plan, or the only home of an unmerged one |
| `waterrise note ID STEP TEXT` | a dated note in the step's protocol (TEXT from stdin if none) |
| `waterrise run ID STEP -- COMMAND ...` | run a command, its output into the step's protocol and the terminal |
| `waterrise convert FILE` | a skeleton from a Markdown plan or an old HTML form |
| `waterrise serve [--open]` | start or join the local service, register this repository, print the URL |
| `waterrise service [status\|stop\|restart\|logs\|run]` | the service itself |
| `waterrise projects [list\|add\|remove] [PATH] [--home repo\|central]` | the registered projects, and where each plans |
| `waterrise projects worktrees [SLUG\|PATH] [DIR\|--default]` | the project's worktree folder on this machine: show it, set it over planning.toml, or drop it |
| `waterrise install [--check VERSION] [--boot\|--no-boot] [--no-path] [--uninstall]` | waterrise itself |

The service is one process per user for every repository, on
`127.0.0.1:8765` (`WATERRISE_PORT` changes it). Its state is in
`$XDG_STATE_HOME/waterrise/` (`%LOCALAPPDATA%\waterrise\state\` on Windows).

**Two homes.** A project keeps its `planning/` in its own repository (`repo`,
the default) or in the central repository,
`$XDG_DATA_HOME/waterrise/plans/<slug>/planning/` (`central`;
`%LOCALAPPDATA%\waterrise\plans\` on Windows), for a
repository whose plans must not be in it, or something that is no repository
at all. The home is in the registry only (`waterrise projects`), so a fresh
clone of a central project has no `planning/` and is told to register it;
never `init` one there. Every command works the same in both homes. In a
central project, waterrise commits the folder in the central repository after
every command that writes it, `check` included, and after every click in the
page; you commit nothing there yourself.

## 11. Before handing a draft to the user

- [ ] `waterrise check` passes.
- [ ] Every decision has context, two to four options, a recommendation and a because.
- [ ] Every step has an owner; user steps say exactly what to do.
- [ ] Risky steps are gated.
- [ ] `waterrise serve` has run, and the user has the URL.

## Reference

<!-- reference:start -->
<!-- generated from waterrise/types.py by `types.reference()`; do not edit by hand -->

#### `planning.toml`

| key | type | required | meaning |
|---|---|---|---|
| `format` | integer | yes | the format version: 1 |
| `user` | string |  | how the pages and `status` name the user (default: git's user.name) |
| `plan_approval` | boolean | default `true` | a plan needs the user's approval to leave draft/ |
| `worktrees` | string |  | the folder this project's worktrees live in, relative to the main checkout, `/` separated, may go up with `..` (default `worktrees`; a machine may override it: `waterrise projects worktrees`) |

#### Keys every item has

| key | type | required | meaning |
|---|---|---|---|
| `kind` | `"plan"` \| `"decision"` \| `"review"` \| `"note"` \| `"report"` | yes | what the file is; must match the id's letter |
| `id` | string | yes | `<letter>-<six base32>`, made by `waterrise new`; never changes |
| `title` | string | yes | the item's name |
| `summary` | string | yes | one or two sentences, shown on cards |
| `created` | date | yes | the day it was written |
| `updated` | date | yes | the day it last changed; not later than today |
| `context` | list of reference |  | items to read first, usually notes |
| `related` | list of reference |  | items worth knowing about |
| `tags` | list of string |  | free words |

#### `kind = "plan"`

| key | type | required | meaning |
|---|---|---|---|
| `why` | prose (Markdown) | yes | why the work is worth doing |
| `scope` | `[scope]` |  | the `[scope]` table |
| `acceptance` | list of string |  | what done means for the whole plan |
| `needs` | list of reference |  | plans that must be done first |
| `touches` | list of string |  | paths or areas it changes |
| `section` | `[[section]]` |  | design notes that belong to this plan only |
| `decision` | `[[decision]]` |  | questions for the user |
| `phase` | `[[phase]]` |  | the stages of the work, each with its steps |
| `risk` | `[[risk]]` |  | what could go wrong |
| `log` | `[[log]]` |  | progress, oldest first; the agent appends |

#### `kind = "decision"`

| key | type | required | meaning |
|---|---|---|---|
| `context` | prose (Markdown) |  | what the user needs to know to answer |
| `question` | string | yes | one question, ending in a question mark |
| `as_of` | date |  | when the facts in `context` were checked |
| `multiple` | boolean | default `false` | more than one option may be chosen |
| `option` | `[[option]]` | yes | two to four options (a resolved decision may keep more) |
| `recommend` | string or list of strings |  | the recommended option id (a list if `multiple`) |
| `because` | string |  | why that option; required with `recommend` |
| `own_answer` | boolean | default `true` | the user may write their own answer |
| `resolved` | list of string |  | option ids, or ["own"]: set by the agent once it has acted on the answer; a decision without it is open |
| `outcome` | string |  | required with `resolved`: what was decided, by whom, and what changed |
| `resolved_on` | date |  | the day it was resolved |
| `final` | boolean | default `false` | settled, and not to be reopened |

#### `kind = "review"`

| key | type | required | meaning |
|---|---|---|---|
| `subject` | prose (Markdown) | yes | what was reviewed, and at which commit |
| `method` | prose (Markdown) |  | how, and how far the findings were checked |
| `finding` | `[[finding]]` |  | the findings |

#### `kind = "note"`

| key | type | required | meaning |
|---|---|---|---|
| `state` | `"draft"` \| `"finished"` | default `"finished"` | draft while it is being written; finished when it can be relied on |
| `section` | `[[section]]` | yes | the text, in sections |

#### `kind = "report"`

| key | type | required | meaning |
|---|---|---|---|
| `state` | `"draft"` \| `"finished"` | default `"finished"` | draft while it is being written; finished when it can be relied on |
| `about` | reference |  | the plan it reports on; without it, a report on the project |
| `file` | string |  | the HTML document, beside this file (default: this file's name, .html) |

#### `[[decision]]` in a plan

| key | type | required | meaning |
|---|---|---|---|
| `id` | string | yes | local id, never renamed or reused |
| `question` | string | yes | one question, ending in a question mark |
| `context` | prose (Markdown) |  | what the user needs to know to answer |
| `as_of` | date |  | when the facts in `context` were checked |
| `multiple` | boolean | default `false` | more than one option may be chosen |
| `option` | `[[option]]` | yes | two to four options (a resolved decision may keep more) |
| `recommend` | string or list of strings |  | the recommended option id (a list if `multiple`) |
| `because` | string |  | why that option; required with `recommend` |
| `own_answer` | boolean | default `true` | the user may write their own answer |
| `resolved` | list of string |  | option ids, or ["own"]: set by the agent once it has acted on the answer; a decision without it is open |
| `outcome` | string |  | required with `resolved`: what was decided, by whom, and what changed |
| `resolved_on` | date |  | the day it was resolved |

#### `[[…option]]`

| key | type | required | meaning |
|---|---|---|---|
| `id` | string | yes | local id of the option |
| `label` | string | yes | what the user reads and picks |
| `hint` | string |  | one line under the label: the consequence of picking it |

#### `[[phase]]`

| key | type | required | meaning |
|---|---|---|---|
| `id` | string | yes | local id |
| `title` | string | yes | what this stage of the work is |
| `goal` | string |  | what is true when the phase is done |
| `step` | `[[step]]` | yes | the steps, in order |

#### `[[phase.step]]`

| key | type | required | meaning |
|---|---|---|---|
| `id` | string | yes | local id, never renamed or reused |
| `title` | string | yes | one checkable outcome |
| `by` | `"agent"` \| `"user"` | yes | who does it |
| `state` | `"open"` \| `"active"` \| `"done"` \| `"blocked"` \| `"dropped"` | default `"open"` | where it is |
| `gate` | boolean | default `false` | the user must approve this step before it starts |
| `needs` | list of reference |  | decisions, steps or phases that come first |
| `details` | prose (Markdown) |  | how, exactly |
| `done_when` | string |  | how to tell it is done |
| `commit` | string |  | the commit that did it |
| `reason` | string |  | required when blocked or dropped |

#### `[scope]`

| key | type | required | meaning |
|---|---|---|---|
| `in` | list of string |  | what the plan does |
| `out` | list of string |  | what it deliberately does not |

#### `[[section]]`

| key | type | required | meaning |
|---|---|---|---|
| `id` | string |  | local id, so the section can be commented on |
| `title` | string | yes | the heading |
| `body` | prose (Markdown) | yes | the text |

#### `[[risk]]`

| key | type | required | meaning |
|---|---|---|---|
| `what` | string | yes | what could go wrong |
| `mitigation` | string |  | what keeps it small |

#### `[[log]]`

| key | type | required | meaning |
|---|---|---|---|
| `on` | date | yes | the day |
| `text` | string | yes | what happened |
| `step` | reference |  | the step it is about |
| `commit` | string |  | the commit |

#### `[[finding]]`

| key | type | required | meaning |
|---|---|---|---|
| `id` | string | yes | local id |
| `title` | string | yes | the finding in one line |
| `severity` | `"critical"` \| `"high"` \| `"medium"` \| `"low"` \| `"info"` | yes | how bad |
| `area` | string |  | where: a host, a path, a component |
| `details` | prose (Markdown) |  | what exactly |
| `evidence` | prose (Markdown) |  | how it was seen: a command and its output, a file and line |
| `state` | `"open"` \| `"confirmed"` \| `"rejected"` \| `"fixed"` \| `"moved"` | default `"open"` | where its triage is |
| `to` | reference |  | the plan (or plan/step) that took it; required when moved |
| `reason` | string |  | why it was rejected |
<!-- reference:end -->
