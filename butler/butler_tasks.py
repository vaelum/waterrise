"""waterrise's own tasks: a test run and a package build, as butler has them.

Both are true of this repository and not of the projects the harness serves,
which is what butler_tasks.py is for.
"""

from butler import ButlerError, arg, task, ui


@task("test", help="run the test suite",
      args=[arg("pytest_args", nargs="*", metavar="ARG",
                help="passed through to pytest (-k, -x, a test path…)")])
def test(ctx, args):
    # pythonpath = ["."] in pyproject.toml: pytest runs against the working
    # tree, with no install and no venv.
    rc = ctx.run(["python3", "-m", "pytest", *args.pytest_args])
    if rc == 0:
        ui.ok("tests passed")
    return rc


def _python(ctx) -> str:
    """The project's dev venv if it has one, else whatever is on PATH."""
    venv = ctx.root / ".venv" / "bin" / "python"
    return str(venv) if venv.is_file() else "python3"


@task("build", help="build the wheel and the sdist into dist/")
def build(ctx):
    """What CI publishes on a tag, buildable here before tagging anything."""
    rc = ctx.run([_python(ctx), "-m", "build", "--outdir", ctx.dist])
    if rc != 0:
        raise ButlerError(
            "the package build failed",
            hint="If `build` itself is missing, it comes with the dev extra:\n"
                 "  python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'")
    for made in sorted(ctx.dist.glob("waterrise-*")):
        ui.ok("built", ctx.disp(made))
    return 0
