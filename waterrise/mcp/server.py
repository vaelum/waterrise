#!/usr/bin/env python3
"""The work-plan MCP server: stdio, newline-delimited JSON-RPC 2.0.

    python3 waterrise/mcp/server.py

description's `agents install claude-code` registers it as `work-plan`, from
its waterrise submodule. In Claude Code it brings the prompt
`/mcp__work-plan__work_plan <plan>` (more easily typed as the
`/work-plan <plan>` skill, which inlines the same text) and the tools
`plan_status` and `wait_for_plan`; see waterrise/mcp/__init__.py for what they
do. Run as a script, it finds the waterrise package two levels up, so it needs
nothing installed but the `waterrise` it calls.

Every tool call runs on its own thread, so a wait that blocks for minutes
leaves the server free to answer pings, list tools and take a cancellation.
"""

from __future__ import annotations

import json
import sys
import threading
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from waterrise import mcp as workplan  # noqa: E402  (needs the path above)

NAME = "work-plan"
VERSION = "1"
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = """\
Works a plan (`planning/` in amos's repositories, read by waterrise) to the end without amos \
coming back to the chat. `plan_status` says what is for you and what waits on amos; \
`wait_for_plan` blocks until amos acts in the planning page. Start with the \
`work_plan` prompt, or follow it: work, wait, work, until the plan is done."""

PLAN_ARG = {"type": "string",
            "description": "The plan: its id (p-xxxxxx), repo:id (e.g. infra-homeserver:p-32a9sd), "
                           "or the path to its TOML file."}

TOOLS = [
    {
        "name": "plan_status",
        "title": "Plan status",
        "description": "What a plan needs now: the steps, answers, comments and moves that are "
                       "for you, what waits on amos, and the plan's page. Reads `waterrise status`; "
                       "changes nothing.",
        "inputSchema": {"type": "object", "properties": {"plan": PLAN_ARG}, "required": ["plan"]},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "wait_for_plan",
        "title": "Wait for amos",
        "description": "Block until amos does something in the plan's page that you can act on (answers "
                       "a decision, approves or disapproves a gate or the plan, ticks a step, comments), "
                       "then return it. Returns at once if there is already work for you. After "
                       "`minutes` with nothing new it returns NOTHING YET: call it again. Use this "
                       "instead of ending your turn or asking amos in chat.",
        "inputSchema": {"type": "object", "properties": {
            "plan": PLAN_ARG,
            "minutes": {"type": "number", "minimum": 0, "maximum": workplan.MAX_WAIT_MINUTES,
                        "description": f"How long to block at most (default {workplan.DEFAULT_WAIT_MINUTES})."},
        }, "required": ["plan"]},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
]

PROMPT = {
    "name": "work_plan",
    "title": "Work a plan to the end",
    "description": "Work a plan continuously: do every step you can, then wait in the planning "
                   "page for amos's answers, approvals and comments, and carry on — no chat needed.",
    "arguments": [{"name": "plan", "description": PLAN_ARG["description"], "required": True}],
}


def prompt_text(plan: str) -> str:
    return f"""\
Work the plan `{plan}` to the end, without amos having to come back to this chat.

amos talks to you only through the plan's page (decisions, approvals, ticks, comments). \
You talk to amos only through the plan file. So this is one long loop:

1. Load the `planning` skill and run `waterrise guide`. Run every planning command as \
`waterrise <command>` in the plan's repository (`plan_status` names it); in a butler \
project pinned to a calendar version (v2026.x), `python butler.py planning <command>` is the same command. \
Follow the guide and the repository's own rules throughout; this prompt changes none of them.
2. Call the `plan_status` tool with plan `{plan}`.
3. If it says WORK, do all of it, in this order:
   - answers: act on each answered decision, then record `resolved`, `outcome`, `resolved_on`;
   - disapprovals: drop the step with amos's note as `reason`, or rewrite it (which asks again);
   - comments: change the plan as asked (or answer in the item), then `waterrise resolve` each;
   - ticked user steps: set them `done`;
   - ready steps: `state = "active"`, do the work, commit, `state = "done"` with `commit`, \
a `[[log]]` entry, `updated`, `waterrise check`. A gated step only right after \
`waterrise gate <id> <step>` exits 0, with its line quoted; on a closed gate, never work around it;
   - folder moves with `waterrise move`.
   Commit as you go, as the repository's conventions say. Never write `planning/answers/`.
   Keep the page current: amos watches it live, and it shows only what is in the file. \
Write each change the moment it happens, never saved up for the end of a phase or of \
the session: `active` when you start a step, `done` with its `commit` and `[[log]]` \
entry as soon as it is finished and before you start the next, a ticked user step \
`done` as soon as you see it, a resolved decision or comment the same way, and \
`waterrise check` after every edit.
4. Anything you need from amos becomes part of the plan, never a chat question: a new \
`[[decision]]` placed in the `needs` of the steps it holds up, a `gate = true` on a risky \
step, a user step for what only amos can do. If a step cannot go on for a reason \
outside the plan, set it `blocked` with a `reason` and add what would unblock it.
5. When `plan_status` says WAITING, tell amos once, briefly, what you need and give the \
page URL (send a push notification too if you have a tool for it). Then call \
`wait_for_plan` with plan `{plan}`.
   - AMOS ACTED or CHANGED: go back to 2.
   - NOTHING YET: call `wait_for_plan` again, at once, without writing anything. Do not \
end your turn because amos is quiet; waiting is the job.
   - ERROR: look at it, fix what is yours to fix, and wait again.
6. Stop only when the plan is DONE (summarise what was done, with commits), or amos tells \
you to stop in a comment or here.

Keep chat output short: one line per finished step or handled answer. The detail \
belongs in the plan's log.
"""


# --------------------------------------------------------------------------- #
# the protocol
# --------------------------------------------------------------------------- #


class Server:
    def __init__(self, out=sys.stdout, cwd: Path | None = None):
        self.out = out
        self.cwd = cwd or Path.cwd()
        self.lock = threading.Lock()
        self.cancelled: dict = {}       # request id -> threading.Event

    def send(self, msg: dict) -> None:
        line = json.dumps(msg, ensure_ascii=False)
        with self.lock:
            self.out.write(line + "\n")
            self.out.flush()

    def reply(self, rid, result=None, error=None) -> None:
        msg = {"jsonrpc": "2.0", "id": rid}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result
        self.send(msg)

    def handle(self, msg: dict) -> None:
        method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method is None:
            return                      # a response to something we never ask
        if method == "notifications/cancelled":
            ev = self.cancelled.get(params.get("requestId"))
            if ev is not None:
                ev.set()
            return
        if rid is None:
            return                      # any other notification: nothing to do
        if method == "initialize":
            asked = params.get("protocolVersion")
            self.reply(rid, {
                "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                "capabilities": {"tools": {}, "prompts": {}},
                "serverInfo": {"name": NAME, "version": VERSION},
                "instructions": INSTRUCTIONS,
            })
        elif method == "ping":
            self.reply(rid, {})
        elif method == "tools/list":
            self.reply(rid, {"tools": TOOLS})
        elif method == "prompts/list":
            self.reply(rid, {"prompts": [PROMPT]})
        elif method == "prompts/get":
            if params.get("name") != PROMPT["name"]:
                self.reply(rid, error={"code": -32602, "message": f"no prompt {params.get('name')!r}"})
                return
            plan = (params.get("arguments") or {}).get("plan", "")
            self.reply(rid, {"description": PROMPT["description"], "messages": [
                {"role": "user", "content": {"type": "text", "text": prompt_text(plan)}}]})
        elif method == "tools/call":
            ev = threading.Event()
            self.cancelled[rid] = ev
            threading.Thread(target=self.call, args=(rid, params, ev), daemon=True).start()
        else:
            self.reply(rid, error={"code": -32601, "message": f"unknown method {method}"})

    def call(self, rid, params: dict, ev: threading.Event) -> None:
        name, args = params.get("name"), params.get("arguments") or {}
        token = (params.get("_meta") or {}).get("progressToken")

        def progress(done: float, total: float) -> None:
            if token is not None:
                self.send({"jsonrpc": "2.0", "method": "notifications/progress",
                           "params": {"progressToken": token, "progress": round(done),
                                      "total": round(total), "message": "waiting on amos"}})
        try:
            if name == "plan_status":
                text = workplan.tool_status(args.get("plan", ""), self.cwd)
            elif name == "wait_for_plan":
                text = workplan.tool_wait(args.get("plan", ""), self.cwd, args.get("minutes"),
                                          cancelled=ev.is_set, progress=progress)
            else:
                self.reply(rid, error={"code": -32602, "message": f"unknown tool {name!r}"})
                return
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        except workplan.PlanError as e:
            result = {"content": [{"type": "text", "text": f"ERROR: {e}"}], "isError": True}
        except Exception:                # noqa: BLE001 — the agent should see it, not lose the server
            result = {"content": [{"type": "text", "text": "ERROR:\n" + traceback.format_exc()}],
                      "isError": True}
        finally:
            self.cancelled.pop(rid, None)
        if not ev.is_set():
            self.reply(rid, result)

    def serve(self, inp=sys.stdin) -> None:
        for line in inp:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                self.send({"jsonrpc": "2.0", "id": None,
                           "error": {"code": -32700, "message": "parse error"}})
                continue
            for m in msg if isinstance(msg, list) else [msg]:
                self.handle(m)


def utf8_stdio() -> None:
    """JSON-RPC over stdio is UTF-8, whatever the console's code page: before
    Python 3.15, Windows' stdio is cp1252 by default, and a reply with an em
    dash reached the client as a byte it could not decode (2026-10-20, in the
    Windows VM). Newlines stay \\n: one message per line, no CR."""
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", newline="\n" if stream is sys.stdout else None)


if __name__ == "__main__":
    utf8_stdio()
    # `--prompt <plan>` prints the prompt instead of serving, for the
    # `/work-plan` skill (skills/work-plan), which inlines it.
    if len(sys.argv) == 3 and sys.argv[1] == "--prompt":
        sys.stdout.write(prompt_text(sys.argv[2]))
    else:
        Server().serve()
