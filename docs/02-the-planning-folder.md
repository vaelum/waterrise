# The planning folder

`waterrise init` makes a repository's `planning/` folder typed. Everything
waterrise knows about the project's plans is in that folder; there is no
database.

```
planning/
  planning.toml   format = 1, user, plan_approval
  draft/          items waiting on you
  todo/           approved; decisions may still be open
  in-progress/    being worked on
  done/           finished, kept as the record
  notes/          context several items share
  reports/        HTML reports and their sidecars
  answers/        <id>.json: your answers and approvals, written by the service
  protocols/      what was noted and run for each step; not committed
```

## The folder is the state

An item's folder is its state. `waterrise move <id> <folder>` checks the
target folder's rules first and then runs `git mv`. With `plan_approval =
true` in `planning.toml`, a plan needs your approval to leave `draft/`, and it
can only go to `in-progress/` once every open question is held by a step that
needs it, so no work starts past an unanswered question.

## Kinds

Each item is one TOML file with a `kind`:

| You have | Write a |
|---|---|
| work with steps, or questions that lead to work | `plan` |
| one question no plan owns, or a settled choice that must not be reopened | `decision` |
| findings of a review or an audit | `review` |
| context several items read first | `note` |
| what was measured or compared, with numbers, tables or charts | `report` |

`waterrise new plan <name>` (or `decision`, `review`, `note`, `report`)
writes one from its template with a fresh id. The id, like `p-3p758x`, never
changes; the file name is a readable slug and may. The page's address, the
answers file and every reference hang on the id, so a move or a rename breaks
nothing.

## Plans

A plan has a `why`, optional `acceptance` criteria, its decisions, and
phases of steps. A decision asks one question, with the facts, two to four
options, a recommendation and the reason for it. A step is one checkable
outcome owned by one side, `by = "agent"` or `by = "user"`, with `done_when`
and `needs`, the decisions, steps or plans that must come first. A step that
is hard to undo or visible outside the machine — a deploy, a release, a push,
deleting data, spending money — gets `gate = true`.

Prose fields hold Markdown, written between three single quotes so nothing
needs escaping:

```toml
[[phase.step]]
  id = "s2-1"
  title = "The release is published"
  by = "agent"
  gate = true
  needs = ["s1-3"]
  done_when = "The tag and its release are public."
```

## Two writers

You, or your agent, write the items. The service writes `answers/<id>.json`
when you click in the page: your picks, approvals, ticks, comments and the
order of the cards. No two writers ever share a file, which is what lets an
agent edit a plan while you answer it.

## Check

`waterrise check` validates the whole folder: unknown keys and the line they
are on, missing fields, a step done before what it needs, a gated step done
without a current approval, a report that loads something remote or holds
something that looks like a credential. It exits 1 on an error, so it can run
in CI.
