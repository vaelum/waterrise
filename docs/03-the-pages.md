# The pages

`waterrise serve` starts the local service, or joins it if it is running,
registers the repository you are in, and prints the page's address. There is
one service per user for every repository, on `127.0.0.1:8765`
(`WATERRISE_PORT` changes it). It answers only on your own machine.

## A repository's page

Each folder is a lane, and each item a card. Drag the cards into the order
you want the work done; agents take work from `todo/` top-down. Dragging a
card to another lane moves the item, with the same rules as
`waterrise move`. A card shows what it needs and what needs it. All lanes are as
tall as the fullest one, and a drop lands anywhere in a lane.

If the project has worktrees, the page lists each with its branch, whether it
is clean, how far it is from the main checkout, and its plans. A card shows the
worktree it is assigned to, and a filter above the lanes shows one worktree's
plans. A worktree that no longer exists shows as gone, and one whose git does not
answer in time shows as unknown. Below the list, the page names the project's
worktree folder and where it is set. On the index, a project shows how many
worktrees it has.

## An item's page

A plan's page has a picker for its worktree, "anywhere" being none. It writes
the same as `waterrise assign`, and an approval is not affected. A plan that lives
in a worktree is shown from there, with its branch on its card, and what you answer
is written there. If picking a worktree makes a different copy count, the page says
so.

The top of every item's page is your to-do list, built from the files: the
decisions to answer, the gates to approve, and your own steps whose `needs`
are all finished.

- **Decisions.** Pick an option, or write your own answer. The recommended
  option is marked, with the reason.
- **Approvals.** Approve or disapprove a gated step, or the plan as a whole,
  with a note. An approval covers the step's text: if the step changes
  afterwards, the page shows one line and **Review changes**. That opens a popup
  with what changed, by phase, step and word, inline or side by side, where you
  answer again. If the text changes while you read, the popup shows the newest
  text and says so, and an answer on the older text is refused. Anything you were
  typing is kept.
- **Your steps.** Tick them when they are done.
- **Comments.** Comment on the item, a section, a phase, a decision, a step
  or a finding. Whoever works the plan answers by changing it, and marks the
  comment handled with a note.

Beside a plan, under your to-do list:

- **Pause and Continue.** Paused, the agent working the plan finishes the step
  it is on and then waits until you press Continue.
- **Progress.** Each phase with how many of its steps are done and a bar, its
  steps with their state, the one being worked on marked; each links to its
  step. At phone width the panel sits above the plan, with Progress folded.
- **The chat.** Ask the agent to change the plan, or ask it something. A message
  is a comment on the whole plan; the agent's answer appears under it, and what
  it did once it is done. It answers while it works the plan with `/work-plan`.

The pages update live while an agent works. Each agent step can keep a
protocol, the commands it ran and what it noted, which you can read at the
step as it is written; the step being worked on has its protocol open, following
the newest line. Near the end of a plan's page, its timeline lists the log and
every note of every step, newest first.

## Two homes

A project keeps its `planning/` in its own repository, the default, or in the
central plans folder (see [Install](01-install.md#where-things-live)), for a
repository whose plans must not be in it, or for something that is no
repository at all. `waterrise projects add PATH --home central` registers one
there; every command works the same in both homes, and in a central project
waterrise commits the folder itself after every change.

## The service

`waterrise service status`, `stop`, `restart` and `logs` manage it.
`install --boot` starts it at login (see [Install](01-install.md)).
