---
name: work-plan
description: Work a plan to the end while amos answers in its planning page — `/work-plan <plan>`, the short name for the work-plan MCP server's `work_plan` prompt.
argument-hint: <plan>
disable-model-invocation: true
---

!`python3 "${CLAUDE_SKILL_DIR}/../../waterrise/mcp/server.py" --prompt "$ARGUMENTS"`
