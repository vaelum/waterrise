---
name: planning
description: Plan, decide and track work in amos's repositories with waterrise — typed plans, decisions, reviews and notes in `planning/`, amos's answers and approvals through the local pages, and approval gates. Use whenever a repository has a `planning/` folder, when amos asks for a plan, a decision, a roadmap, an approval or "what is waiting on me", and before acting on any step of a plan.
---

# Planning with waterrise

Plans in amos's repositories live in `planning/`. A `planning/planning.toml`
means the folder is typed (format 1): each item is a TOML file with a `kind`
(`plan`, `decision`, `review`, `note`) and an `id` (`p-5cyzm7`) that never
changes. The folder is the state: `draft/`, `todo/`, `in-progress/`, `done/`,
and `notes/` for shared context.

waterrise is the tool that reads, checks and serves these folders. It is
installed once per machine (`waterrise --version`), so every repository is
read by the same version. In a butler project pinned to a calendar version
(v2026.x and later; butler's 0.x versions came before waterrise),
`python butler.py planning <command>` runs the installed `waterrise <command>`
and is the same thing; below, `waterrise` stands for either.

## The rules

1. **Before writing or changing anything in `planning/`, run
   `waterrise guide`.** The guide, not this skill, is the rulebook, and it
   matches the installed waterrise, whichever repository you are in. It has
   the format.
2. `waterrise new <kind> <name>` makes an item with a fresh id. Never invent,
   edit or reuse an id.
3. `waterrise status` (and `waterrise list decisions`, `waterrise list steps
   --by user`) says what amos has answered, commented, ticked and approved.
   Never read or parse `planning/answers/` by hand.
4. To ask amos something, write it as a decision in the item, run
   `waterrise serve`, and give amos the URL it prints. Do not ask the same
   questions in chat. amos's own steps show on the page's to-do only once
   everything in their `needs` is done, so get `needs` right.
5. **Never write anything under `planning/answers/`,** and never call the
   planning service's API with `curl` or a script. Only amos's clicks write
   answers and approvals. The only exceptions are the commands
   `waterrise resolve` (a comment handled) and `waterrise convert`.
6. **A step with `gate = true` runs only after
   `waterrise gate <id> <step>` exits 0,** run just before the action, with
   its output line quoted. If it fails, stop, say why, and give amos the URL.
   Record progress on a gated step in `[[log]]`, not in the step. An
   approval covers the item's text: once a plan is approved, change only
   `state`, `commit`, `reason` and the `[[log]]` without asking again.
7. After every change, run `waterrise check`. Move items with
   `waterrise move <id> <folder>`, never a bare `git mv`.
8. If `waterrise` is not installed, say so and give the install command
   (`git clone https://github.com/vaelum/waterrise.git && cd waterrise &&
   python3 -m waterrise install`); install it only when asked. A butler
   project pinned to a 0.x version runs its own older planning code under
   `python butler.py planning`; use `waterrise` there, and mention that
   bumping the pin would make the two the same.

## The commands

| command | does |
|---|---|
| `guide` | the rules, the format and its reference tables |
| `new KIND NAME` | a new item from its template, with a fresh id (`--folder`) |
| `check [--strict]` | validate the whole folder; exit 1 on an error |
| `status [ID]` | what waits on amos and on you |
| `list [items\|decisions\|steps\|approvals\|comments\|findings] [--by agent\|user] [--everywhere]` | one line each |
| `show ID` | an item as text |
| `move ID FOLDER` | move it, after checking the target folder's rules |
| `gate ID [STEP]` | exit 0 only on amos's current approval |
| `resolve ID COMMENT --note TEXT` | mark amos's comment handled |
| `convert FILE` | a typed skeleton from a Markdown plan or an old HTML form |
| `serve` | start or join the one local service, register this repository, print the URL |
| `service [status\|stop\|restart\|logs]` | the service itself |
| `install [--check VERSION] [--boot]` | waterrise itself: its venv, the launcher, the unit |

## The four modes

| mode | amos, in the page | you |
|---|---|---|
| **Decide** | picks an option or writes an own answer | write the decisions, `serve`, give the URL; act on the answers, then record `resolved`, `outcome`, `resolved_on` |
| **Extend** | comments on any part | read comments in `status`, change the item, `waterrise resolve` each |
| **Track** | watches, ticks their own steps | set `state` (`active`, then `done` with `commit`), append `[[log]]`, `waterrise check` |
| **Gate** | approves or disapproves a step, or a plan leaving `draft/` | `waterrise gate` right before the action; stop on a closed gate |

Gate every step that is hard to undo or visible outside the machine: a push to
a main branch, a deploy, a release, deleting data, rotating credentials,
sending something, spending money. When unsure, gate it.

How the service, the pages and the gates work across amos's repositories is in
the knowledge base: `practices/planning` (homelab skill, `/api/doc/practices/planning`).
