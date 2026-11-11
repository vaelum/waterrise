# Working with an agent

waterrise is built so that an agent asks in the plan rather than in a chat,
and stops before anything it cannot take back.

## What the agent follows

`waterrise guide` prints the rulebook for agents: how to write each kind of
item, how to write a decision you can answer without the chat, how to keep a
plan current while working, and how approvals work. Point your agent at it,
or install the skills below.

The heart of it:

- **Questions go into the plan.** Each as a decision with the facts, the
  options and a recommendation. The agent runs `waterrise serve` and gives
  you the address; you answer in the page.
- **Gates are checked, not trusted.** Right before a gated step, the agent
  runs `waterrise gate <id> <step>`. It exits 0 only if you approved that
  step in the page and the step has not changed since, and its output line is
  what a permission hook or a person reading the transcript can check. On any
  other answer the agent stops.
- **The agent never writes your answers.** Only the service writes
  `answers/`; an approval an agent wrote would show in git.
- **Progress is in the file.** Steps go `active` and `done` with the commit
  that did them, with a log entry, as the work happens, so the page is
  current while you watch it.

## With Claude Code

The repository has two skills and an MCP server:

- `skills/planning`, the short version of the rules, loaded whenever a
  repository has a `planning/` folder;
- `skills/work-plan`, the `/work-plan <plan>` command: work a plan to the end
  while you answer in its page;
- `waterrise/mcp/server.py`, the `work-plan` MCP server, with the tools
  `plan_status` (what is for the agent now, and what waits on you) and
  `wait_for_plan` (block until you act in the page).

To use them, link the skill folders from your checkout and register the
server:

```
ln -s "$PWD/skills/planning" ~/.claude/skills/planning
ln -s "$PWD/skills/work-plan" ~/.claude/skills/work-plan
claude mcp add --scope user work-plan -- python3 "$PWD/waterrise/mcp/server.py"
```

The work-plan skill runs the server from its own folder's checkout, so link
it rather than copy it.

## While it works

A plan's page is where you steer the agent working it with `/work-plan`:

- **Pause and Continue**, at the top of the side panel. Paused, the agent
  finishes the step it is on, commits and records it, starts nothing new, and
  waits until you press Continue.
- **The chat**, at the bottom of the side panel. A message is a comment on the
  whole plan, so the agent gets it at once; it answers under your message
  (`waterrise reply`), makes the change, and marks it done with what changed. A
  message sent while no agent works the plan waits for the next one.
- **Progress, the timeline and the protocols**: the side panel shows each
  phase and step as it goes, the plan's timeline its log and every note newest
  first, and the step being worked on has its protocol open and following.

## Several agents

`/work-plan` may work independent steps at once. When two or more agent steps
are ready, have no gate and touch different files, the agent hands each to a
subagent, which works that step only: it keeps the step's protocol and commits
its own files by path, and never edits the plan, your answers or another step's
protocol. The agent you started stays the only writer of the plan file: it sets
each step active before handing it off, checks what comes back against the
step's `done_when`, and records it done. Gated steps, your answers, comments and
the chat stay with it, and steps that touch the same files are worked in order.
