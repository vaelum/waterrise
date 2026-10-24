# The pages

`waterrise serve` starts the local service, or joins it if it is running,
registers the repository you are in, and prints the page's address. There is
one service per user for every repository, on `127.0.0.1:8765`
(`WATERRISE_PORT` changes it). It answers only on your own machine.

## A repository's page

Each folder is a lane, and each item a card. Drag the cards into the order
you want the work done; agents take work from `todo/` top-down. Dragging a
card to another lane moves the item, with the same rules as
`waterrise move`. A card shows what it needs and what needs it.

## An item's page

The top of every item's page is your to-do list, built from the files: the
decisions to answer, the gates to approve, and your own steps whose `needs`
are all finished.

- **Decisions.** Pick an option, or write your own answer. The recommended
  option is marked, with the reason.
- **Approvals.** Approve or disapprove a gated step, or the plan as a whole,
  with a note. An approval covers the step's text: if the step changes
  afterwards, the page asks again and shows what changed since your answer.
- **Your steps.** Tick them when they are done.
- **Comments.** Comment on the item, a section, a phase, a decision, a step
  or a finding. Whoever works the plan answers by changing it, and marks the
  comment handled with a note.

The pages update live while an agent works. Each agent step can keep a
protocol, the commands it ran and what it noted, which you can read at the
step as it is written.

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
