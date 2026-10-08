"""Finding and running FFmpeg.

Safety rules followed here:

* FFmpeg is always started with an argument *list* and no shell, so characters
  like ``&``, ``'`` or ``%`` in file names are never interpreted as commands.
* File names are passed as absolute ``file:`` URLs. This stops FFmpeg from
  mistaking a name like ``-y.mp4`` for an option, or ``concat:...`` for a
  special protocol.
* We locate ``ffmpeg.exe`` ourselves and only accept absolute folders. Windows
  would otherwise also look in the *current* folder, which (when launched from
  Explorer) is the folder of the file being converted, so a stray
  ``ffmpeg.exe`` sitting next to a downloaded video could be run.

Progress: FFmpeg is asked (``-progress pipe:1``) to print lines like
``out_time_us=1500000`` to its standard output every half second. We compare
that to the input's duration, which FFmpeg prints in its log
(``Duration: 00:00:05.00``).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .errors import FFmpegNotFoundError

FFMPEG_ENV_VAR = "CLICKVERT_FFMPEG"
EXE_NAME = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
STDERR_TAIL_LINES = 60

# Stops a console window from flashing up when Clickvert runs without one.
_CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")

INSTALL_HINT = "Install it with:  winget install Gyan.FFmpeg  and then try again."

# Called with (seconds_done, total_seconds_or_None) from a background thread.
TimeCallback = Callable[[float | None, float | None], None]


def find_ffmpeg() -> Path:
    """Return the absolute path of ffmpeg.exe, or raise FFmpegNotFoundError."""
    override = os.environ.get(FFMPEG_ENV_VAR, "").strip().strip('"')
    if override:
        path = Path(override)
        if path.is_absolute() and path.is_file():
            return path
        raise FFmpegNotFoundError(
            f"The {FFMPEG_ENV_VAR} setting points to “{override}”, but FFmpeg isn't there. "
            "It must be the full path to ffmpeg.exe."
        )

    if getattr(sys, "frozen", False):  # a packaged Clickvert.exe with FFmpeg beside it
        bundled = Path(sys.executable).parent / EXE_NAME
        if bundled.is_file():
            return bundled

    found = search_path(os.environ.get("PATH", ""))
    if found:
        return found
    raise FFmpegNotFoundError(f"Clickvert needs FFmpeg, but couldn't find it. {INSTALL_HINT}")


def search_path(path_value: str) -> Path | None:
    """Find ffmpeg in a PATH string, ignoring relative (current-folder) entries."""
    for entry in path_value.split(os.pathsep):
        entry = entry.strip().strip('"')
        if not entry:
            continue
        folder = Path(entry)
        if not folder.is_absolute():
            continue
        candidate = folder / EXE_NAME
        if candidate.is_file():
            return candidate
    return None


def file_url(path: Path) -> str:
    """Turn an absolute path into an FFmpeg ``file:`` URL (see module docstring)."""
    if not path.is_absolute():
        raise ValueError(f"expected an absolute path, got {path!r}")
    return f"file:{path}"


def parse_duration(line: str) -> float | None:
    match = _DURATION_RE.search(line)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_progress_line(line: str) -> float | None:
    """Return seconds of output written, from an ``out_time_us=`` progress line."""
    key, _, value = line.strip().partition("=")
    if key != "out_time_us":
        return None
    try:
        return max(int(value), 0) / 1_000_000
    except ValueError:  # "N/A" before the first frame is written
        return None


def is_progress_block_end(line: str) -> bool:
    return line.startswith("progress=")


@dataclass
class FFmpegOutcome:
    returncode: int
    cancelled: bool
    command: list[str]
    stderr_tail: list[str] = field(default_factory=list)

    @property
    def last_error_line(self) -> str:
        for line in reversed(self.stderr_tail):
            if line.strip():
                return line.strip()
        return ""

    def describe(self) -> str:
        return (
            f"Command: {subprocess.list2cmdline(self.command)}\n"
            f"Exit code: {self.returncode}\n"
            "FFmpeg output (last lines):\n" + "\n".join(self.stderr_tail)
        )


def run_ffmpeg(
    ffmpeg: Path,
    args: Sequence[str],
    *,
    cancel_event: threading.Event | None = None,
    on_time: TimeCallback | None = None,
) -> FFmpegOutcome:
    """Run FFmpeg once and wait for it, unless ``cancel_event`` is set.

    If cancelled, FFmpeg is stopped immediately. The caller deletes the partial output.
    """
    command = [str(ffmpeg), "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats", *args]
    stderr_tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
    duration: list[float | None] = [None]
    latest_time: list[float | None] = [None]

    proc = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=_CREATION_FLAGS,
    )

    def read_stderr() -> None:
        assert proc.stderr is not None
        for raw in proc.stderr:
            line = raw.decode("utf-8", errors="replace").rstrip()
            stderr_tail.append(line)
            if duration[0] is None:
                duration[0] = parse_duration(line)

    def read_stdout() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace")
            seconds = parse_progress_line(line)
            if seconds is not None:
                latest_time[0] = seconds
            elif is_progress_block_end(line) and on_time is not None:
                on_time(latest_time[0], duration[0])

    readers = [threading.Thread(target=read_stderr, daemon=True), threading.Thread(target=read_stdout, daemon=True)]
    for reader in readers:
        reader.start()

    cancelled = False
    try:
        while True:
            try:
                proc.wait(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    break
    finally:
        # Covers cancel, Ctrl+C and unexpected errors: never leave FFmpeg running.
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        for reader in readers:
            reader.join(timeout=5)
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()

    return FFmpegOutcome(
        returncode=proc.returncode,
        cancelled=cancelled,
        command=command,
        stderr_tail=list(stderr_tail),
    )
