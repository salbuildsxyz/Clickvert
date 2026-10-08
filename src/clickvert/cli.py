"""Command-line entry point.

Examples::

    python -m clickvert convert --to gif "C:\\Videos\\holiday.mp4"
    python -m clickvert formats

In Milestone 3, File Explorer's right-click menu will run the ``convert``
command, with the clicked file's path.

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
from .errors import ClickvertError, ConversionCancelledError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clickvert", description="Convert media files with FFmpeg.")
    parser.add_argument("--version", action="version", version=f"clickvert {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    convert = commands.add_parser("convert", help="convert a file; the result is saved next to it")
    convert.add_argument("--to", required=True, choices=sorted({c.target for c in CONVERSIONS}), help="output format")
    convert.add_argument("file", help="the file to convert")
    convert.add_argument("-q", "--quiet", action="store_true", help="don't show progress")
    convert.add_argument("--verbose", action="store_true", help="show FFmpeg's details when something fails")

    commands.add_parser("formats", help="list supported conversions")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _make_output_streams_tolerant()
    args = build_parser().parse_args(argv)

    if args.command == "formats":
        for c in CONVERSIONS:
            print(f"{c.source.upper():>4} -> {c.target.upper()}")
        return 0

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
