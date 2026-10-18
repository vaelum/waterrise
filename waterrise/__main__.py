"""`waterrise …`: the command line, and what `python butler.py planning …` runs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, ui
from .command import Node
from .context import prog, resolve
from .errors import WaterriseError


def _global_flags() -> argparse.ArgumentParser:
    """The flags every level takes, as the harness has them.

    SUPPRESS defaults, so a subcommand's copy of --dry-run does not overwrite
    one given before it; they are filled in after parsing.
    """
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS,
                   help="show more about what waterrise is doing")
    p.add_argument("-n", "--dry-run", action="store_true", default=argparse.SUPPRESS,
                   help="print what would change, without changing it")
    p.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS,
                   help="disable coloured output")
    p.add_argument("--yes", action="store_true", default=argparse.SUPPRESS,
                   help="skip confirmation prompts (for unattended runs)")
    return p


def _add_level(parser: argparse.ArgumentParser, nodes: list[Node],
               globals_: argparse.ArgumentParser, depth: int) -> None:
    sub = parser.add_subparsers(dest=f"_level{depth}", metavar="<command>", required=True)
    for node in nodes:
        p = sub.add_parser(node.name, help=node.help, description=node.help,
                           parents=[globals_], epilog=node.epilog,
                           formatter_class=argparse.RawDescriptionHelpFormatter)
        for a in node.args:
            p.add_argument(*a.flags, **a.kwargs)
        if node.func is not None:
            p.set_defaults(_func=node.func)
        if node.children:
            _add_level(p, node.children, globals_, depth + 1)


def build_parser(nodes: list[Node]) -> argparse.ArgumentParser:
    globals_ = _global_flags()
    parser = argparse.ArgumentParser(
        prog=prog(), parents=[globals_], formatter_class=argparse.RawDescriptionHelpFormatter,
        description="typed plans: check, status, decisions, approvals, and the local pages")
    parser.set_defaults(_func=None)
    parser.add_argument("--version", action="store_true", help="print the version, and exit")
    _add_level(parser, nodes, globals_, depth=0)
    return parser


def main(argv: list[str] | None = None, *, cwd: Path | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ui.init("--no-color" in argv)
    # Before parsing: the subcommand is required, and `--version` alone must
    # not trip that.
    if "--version" in argv and all(a.startswith("-") for a in argv):
        ui.plain(f"waterrise {__version__}")
        return 0

    from .commands import nodes

    parser = build_parser(nodes())
    args = parser.parse_args(argv)
    for flag in ("verbose", "dry_run", "no_color", "yes"):
        setattr(args, flag, getattr(args, flag, False))
    ui.init(args.no_color)
    if args._func is None:
        parser.print_help()
        return 2
    try:
        ctx = resolve(cwd)
        ctx.dry_run, ctx.verbose, ctx.args = args.dry_run, args.verbose, args
        return args._func(ctx, args) or 0
    except WaterriseError as e:
        ui.err(e.message, e.hint)
        return e.code
    except KeyboardInterrupt:
        ui.plain()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
