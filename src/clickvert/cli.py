"""Command-line entry point.

Examples::

    python -m clickvert convert --to gif "C:\\Videos\\holiday.mp4"
    python -m clickvert convert --to gif --window "C:\\Videos\\holiday.mp4"
    python -m clickvert formats
    python -m clickvert install --dry-run
    python -m clickvert uninstall

File Explorer's right-click menu runs ``convert --window`` with the clicked
file's path (see ``shell_integration.py``).

Exit codes: 0 success, 2 bad command-line usage, and otherwise the
``exit_code`` of the error (see ``errors.py``).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import __version__
from .conversions import CONVERSIONS
from .converter import ConversionJob
from .errors import ClickvertError, ConversionCancelledError, IntegrationError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clickvert", description="Convert media files with FFmpeg.")
    parser.add_argument("--version", action="version", version=f"clickvert {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    convert = commands.add_parser("convert", help="convert a file; the result is saved next to it")
    convert.add_argument("--to", required=True, choices=sorted({c.target for c in CONVERSIONS}), help="output format")
    convert.add_argument("file", help="the file to convert")
    convert.add_argument("-q", "--quiet", action="store_true", help="don't show progress")
    convert.add_argument("--window", action="store_true", help="show a progress window instead of terminal output")
    convert.add_argument("--verbose", action="store_true", help="show FFmpeg's details when something fails")

    commands.add_parser("formats", help="list supported conversions")

    for name, help_text in [("install", "add Clickvert to File Explorer's right-click menu"),
                            ("uninstall", "remove Clickvert from File Explorer's right-click menu")]:
        menu = commands.add_parser(name, help=help_text)
        menu.add_argument("--dry-run", action="store_true", help="only show what would change")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _make_output_streams_tolerant()
    args = build_parser().parse_args(argv)

    if args.command == "formats":
        for c in CONVERSIONS:
            print(f"{c.source.upper():>4} -> {c.target.upper()}")
        return 0

    if args.command in ("install", "uninstall"):
        try:
            return _install(args.dry_run) if args.command == "install" else _uninstall(args.dry_run)
        except ClickvertError as exc:
            print(f"Error: {exc.user_message}", file=sys.stderr)
            if exc.details:
                print(exc.details, file=sys.stderr)
            return exc.exit_code

    if args.window:
        from .progress_window import run_window  # loads tkinter only when needed

        return run_window(args.file, args.to)

    job = ConversionJob(args.file, args.to, on_progress=None if args.quiet else _print_progress)
    try:
        output = job.run()
    except KeyboardInterrupt:
        _end_progress_line(args.quiet)
        print("Cancelled. No file was created.", file=sys.stderr)
        return ConversionCancelledError.exit_code
    except ClickvertError as exc:
        _end_progress_line(args.quiet)
        print(f"Error: {exc.user_message}", file=sys.stderr)
        if args.verbose and exc.details:
            print(f"\n{exc.details}", file=sys.stderr)
        return exc.exit_code

    _end_progress_line(args.quiet)
    print(output)
    return 0


def _install(dry_run: bool) -> int:
    from . import shell_integration as shell
    from .ffmpeg import find_ffmpeg

    pythonw, package_dir = shell.default_launcher()
    if not pythonw.is_file():
        raise IntegrationError(f"Couldn't find pythonw.exe next to {sys.executable}.")
    keys = shell.plan(pythonw, package_dir)

    if dry_run:
        print("Dry run: nothing was changed. Installing would first remove any existing")
        print("Clickvert menu entries, then write these registry keys:\n")
        print(shell.describe_keys(keys))
        return 0

    shell.install(shell.WindowsRegistry(), keys)
    print("Clickvert was added to the right-click menu for: " + ", ".join(shell.source_extensions()))
    print('On Windows 11, right-click a file and choose "Show more options" (or press Shift + right-click).')
    print(f"The menu runs Clickvert from {package_dir}. If you move this folder, run install again.")
    try:
        find_ffmpeg()
    except ClickvertError as exc:
        print(f"\nWarning: {exc.user_message}", file=sys.stderr)
    return 0


def _uninstall(dry_run: bool) -> int:
    from . import shell_integration as shell

    registry = shell.WindowsRegistry()
    if dry_run:
        existing = [k for k in shell.menu_keys() if registry.key_exists(k)]
        print("Dry run: nothing was changed.")
        if not existing:
            print("Clickvert's right-click menu isn't installed, so there's nothing to remove.")
        for key in existing:
            print(f"Would delete [{shell.full_name(key)}] and everything inside it")
        return 0

    removed = shell.uninstall(registry)
    if removed:
        print("Clickvert was removed from the right-click menu. Removed:")
        for key in removed:
            print(f"  {shell.full_name(key)}")
    else:
        print("Clickvert's right-click menu wasn't installed. Nothing to remove.")
    return 0


def _make_output_streams_tolerant() -> None:
    # When output is redirected, Windows may use an old text encoding that has
    # no emoji. Print a "?" instead of crashing on a file name like "🎬.mp4".
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def _print_progress(fraction: float | None) -> None:
    text = "Converting..." if fraction is None else f"Converting... {fraction:4.0%}"
    print(f"\r{text:<24}", end="", file=sys.stderr, flush=True)


def _end_progress_line(quiet: bool) -> None:
    if not quiet:
        print(file=sys.stderr)
