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


@task("windows-test", help="run the suite in the Windows test VM on this machine (tools/windows-vm)",
      args=[arg("--ref", default="HEAD", help="the commit to test (default HEAD; not uncommitted changes)"),
            arg("--python", default="3.13,3", help="the Pythons to run it under (default 3.13,3)"),
            arg("--e2e", action="store_true",
                help="also install --boot, reboot the VM, and check the service after logon")])
def windows_test(ctx, args):
    # The VM is built once with `python3 tools/windows-vm/vm.py build`; each
    # run starts from its clean snapshot (tools/windows-vm/README.md).
    argv = ["python3", str(ctx.root / "tools" / "windows-vm" / "vm.py"), "test",
            "--ref", args.ref, "--python", args.python]
    rc = ctx.run(argv + (["--e2e"] if args.e2e else []))
    if rc == 0:
        ui.ok("tests passed on Windows")
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
