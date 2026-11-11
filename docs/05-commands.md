# Commands

`waterrise --help` lists them, and `waterrise <command> --help` explains one.

| Command | Does |
|---|---|
| `waterrise guide` | print the rules and the format, for agents and people |
| `waterrise init` | make `planning/` typed |
| `waterrise new KIND NAME` | a new item with a fresh id |
| `waterrise check [--strict] [--json]` | validate the whole folder; exit 1 on an error |
| `waterrise status [ID] [--json]` | what waits on you and on the agent |
| `waterrise list [items\|decisions\|steps\|approvals\|comments\|findings] [--all] [--by agent\|user] [--everywhere] [--worktree NAME] [--json]` | one line each; open ones unless `--all` |
| `waterrise show ID` | an item as text |
| `waterrise move ID FOLDER` | move it, after checking the folder's rules |
| `waterrise gate ID [STEP]` | exit 0 only if approved and unchanged |
| `waterrise resolve ID COMMENT --note TEXT` | mark a comment handled |
| `waterrise reply ID COMMENT TEXT` | answer a comment or a chat message, without marking it handled |
| `waterrise assign ID NAME\|--none` | assign a plan to a worktree, or to none |
| `waterrise worktree add NAME [--branch B] [--from REF] [--plan ID]` | a worktree in the project's worktree folder (`worktrees/` by default) |
| `waterrise worktree list [--all] [--json]` | name, branch, clean or not, ahead/behind, path and plans |
| `waterrise worktree remove NAME` | `git worktree remove`; refused if dirty, holding an open plan, or the only home of an unmerged one |
| `waterrise note ID STEP TEXT` | a dated note in the step's protocol |
| `waterrise run ID STEP -- COMMAND ...` | run a command, its output into the step's protocol and the terminal |
| `waterrise convert FILE` | a typed skeleton from a Markdown plan |
| `waterrise serve [--open]` | start or join the local service, register this repository, print the address |
| `waterrise service [status\|stop\|restart\|logs\|run]` | the service itself |
| `waterrise projects [list\|add\|remove] [PATH] [--home repo\|central]` | the registered projects, and where each plans |
| `waterrise projects worktrees [SLUG\|PATH] [DIR\|--default]` | the project's worktree folder on this machine |
| `waterrise install [--boot\|--no-boot] [--no-path] [--uninstall]` | waterrise itself |
