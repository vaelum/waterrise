# Commands

`waterrise --help` lists them, and `waterrise <command> --help` explains one.

| Command | Does |
|---|---|
| `waterrise guide` | print the rules and the format, for agents and people |
| `waterrise init` | make `planning/` typed |
| `waterrise new KIND NAME` | a new item with a fresh id |
| `waterrise check [--strict] [--json]` | validate the whole folder; exit 1 on an error |
| `waterrise status [ID] [--json]` | what waits on you and on the agent |
| `waterrise list [items\|decisions\|steps\|approvals\|comments\|findings] [--all] [--by agent\|user] [--everywhere] [--json]` | one line each; open ones unless `--all` |
| `waterrise show ID` | an item as text |
| `waterrise move ID FOLDER` | move it, after checking the folder's rules |
| `waterrise gate ID [STEP]` | exit 0 only if approved and unchanged |
| `waterrise resolve ID COMMENT --note TEXT` | mark a comment handled |
| `waterrise note ID STEP TEXT` | a dated note in the step's protocol |
| `waterrise run ID STEP -- COMMAND ...` | run a command, its output into the step's protocol and the terminal |
| `waterrise convert FILE` | a typed skeleton from a Markdown plan |
| `waterrise serve [--open]` | start or join the local service, register this repository, print the address |
| `waterrise service [status\|stop\|restart\|logs\|run]` | the service itself |
| `waterrise projects [list\|add\|remove] [PATH] [--home repo\|central]` | the registered projects, and where each plans |
| `waterrise install [--boot\|--no-boot] [--no-path] [--uninstall]` | waterrise itself |
