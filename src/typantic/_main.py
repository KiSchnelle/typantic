"""Console-script bootstrap for a Typer app.

:func:`make_main` builds the ``main()`` an app exposes as its console script. It
answers ``--version`` from package metadata *before* the (possibly heavy) command
modules are imported, hands shell-completion requests straight to Typer, runs the
app inside a caller-supplied context, and logs the wall-clock duration of a real
run. The app supplies only a loader for its Typer app and its distribution name.

This is deliberately not what :mod:`typantic._cli` does for typantic's own entry
point: that one declares ``--version`` as an eager Typer callback, which is
simpler but only works because typantic has no expensive imports to defer. An app
that pulls a heavy stack at import time needs the argv short-circuit here.
"""

import logging
import os
import sys
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from importlib.metadata import version
from pathlib import Path

import typer

_VERSION_FLAGS = {"--version", "-V"}
_HELP_FLAGS = {"--help", "-h", "help"}
# Meta-operations (introspect / template, not a real run) that exit 0 without
# doing work; the timing log is skipped for them so a machine-readable stdout
# (e.g. the JSON Schema from --schema, which a web front-end parses) stays
# uncontaminated.
_META_FLAGS = _HELP_FLAGS | {"--schema", "--generate-config"}


def _wants_version(args: list[str]) -> bool:
    """Whether a top-level ``--version`` / ``-V`` (or ``version``) was requested.

    Only program-level tokens count: scanning stops at the first non-option token
    (a subcommand), so a ``--version`` that is really a subcommand's option value
    does not trigger the short-circuit.
    """
    for arg in args:
        if arg in _VERSION_FLAGS:
            return True
        if not arg.startswith("-"):
            break
    return bool(args) and args[0] == "version"


def _is_autocompleting() -> bool:
    """Whether this is a shell-completion invocation (not a real run).

    Completion is driven by *this* program's ``_<PROG>_COMPLETE=complete_<shell>``
    environment variable (typer 0.26 no longer injects a ``__complete`` token into
    argv). ``<PROG>`` is derived exactly as click derives it, from ``argv[0]``'s
    basename with ``-`` and ``.`` both mapped to ``_`` -- so a program named
    ``my.tool`` is looked up as ``_MY_TOOL_COMPLETE``, which is the variable click
    actually reads. Deriving it any other way makes the completion request fall
    through to a real run.
    """
    prog_name = Path(sys.argv[0]).name
    prog_token = prog_name.replace("-", "_").replace(".", "_").upper()
    completion_request = os.environ.get(f"_{prog_token}_COMPLETE") or ""
    return completion_request.startswith(("complete", "source"))


def make_main(
    load_app: Callable[[], typer.Typer],
    *,
    package_name: str,
    run_context: Callable[[], AbstractContextManager[object]] | None = None,
) -> Callable[[], None]:
    """Build a ``main()`` entry point for a Typer app.

    Args:
        load_app: Zero-argument callable returning the :class:`typer.Typer` app.
            Called only after the version short-circuit, so an app whose command
            modules are expensive to import (e.g. pulling torch) stays instant
            for ``--version``. A light app may simply pass ``lambda: app``.
        package_name: Installed distribution name, used to answer ``--version``
            from package metadata and to name the run logger.
        run_context: Optional zero-argument callable returning a context manager
            to wrap the run in -- typically a logging setup that must be torn
            down even when the command raises. Entered *after* the version and
            completion short-circuits, so neither pays for it.

    Returns:
        The ``main`` callable to expose as the package's console-script entry.

    """

    def main() -> None:
        args = sys.argv[1:]

        # Answer the version request before importing the (heavy) command modules.
        if _wants_version(args):
            typer.echo(version(package_name))
            return

        if _is_autocompleting():
            # Completions go to stdout and must not pay for the run context; let
            # Typer compute them without entering it.
            load_app()()
            return

        invoked_meta = any(arg in _META_FLAGS for arg in args)
        module_logger = logging.getLogger(package_name)
        start_time = time.monotonic()

        with nullcontext() if run_context is None else run_context():
            try:
                # Inside the guard so an import error in the (heavy) command
                # modules is reported through the context and exits cleanly,
                # rather than as a raw traceback.
                app = load_app()
                app()
            except SystemExit as system_exit:
                # A non-zero code is an error status and propagates unchanged; a
                # clean exit (0, or None from a bare sys.exit()) is swallowed so
                # main() returns. invoked_meta only gates the timing log: a
                # meta/introspection op (--help/--schema/--generate-config) exits
                # 0 without a real run, so its machine-readable stdout stays clean.
                if system_exit.code not in (0, None):
                    raise
                if not invoked_meta:
                    module_logger.info(
                        "Execution took %.2f minutes.",
                        (time.monotonic() - start_time) / 60,
                    )
            except Exception:
                module_logger.exception(
                    "An error occurred while running the application.",
                )
                raise SystemExit(1) from None  # non-zero exit on crash

    return main
