"""The command tree: one node type for branches and leaves, as in the harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

Handler = Callable[..., int | None]


@dataclass(frozen=True)
class Arg:
    """One argparse argument, declared where the command is declared."""
    flags: tuple[str, ...]
    kwargs: dict[str, Any]


def arg(*flags: str, **kwargs: Any) -> Arg:
    return Arg(flags, kwargs)


@dataclass
class Node:
    name: str
    help: str = ""
    args: list[Arg] = field(default_factory=list)
    # Leaf nodes have a handler; branch nodes have children.
    func: Handler | None = None
    children: list["Node"] = field(default_factory=list)
    epilog: str | None = None
