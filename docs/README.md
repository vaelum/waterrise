# waterrise

waterrise keeps a project's plans in the project. Every plan, decision,
review, note and report is a typed file in the repository's `planning/`
folder. `waterrise check` validates the folder like code. One local service
per user turns the files into pages, and the pages are where questions get
answered and risky steps approved.

It was written for working with a coding agent. The agent writes its
questions into the plan, each with the facts, the options and a
recommendation, and you answer them in the page whenever you get to it. A
step that is hard to undo carries a gate, and the agent may only take it
once you have approved that step in the page and it has not changed since.

## The pages

| Page | Read it when |
|---|---|
| [Install](01-install.md) | You want `waterrise` on your machine, or want to know where it keeps things. |
| [The planning folder](02-the-planning-folder.md) | You write plans by hand, or want to know what an agent writes. |
| [The pages](03-the-pages.md) | You answer decisions, approve steps and comment. |
| [Working with an agent](04-working-with-an-agent.md) | You let an agent plan and work through waterrise. |
| [Commands](05-commands.md) | You want the whole command list. |

`waterrise guide` prints the full rulebook, written for agents: how to write
each kind of item, the four ways of working, approvals, and the reference
tables generated from waterrise's own types. It is also in the repository as
`waterrise/GUIDE.md`.

waterrise is MIT-licensed.
