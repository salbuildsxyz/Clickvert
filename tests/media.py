"""Helpers that create tiny sample media files with FFmpeg, and inspect results.

Sample files are generated during the tests rather than stored in the repo.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from clickvert.errors import FFmpegNotFoundError
from clickvert.ffmpeg import file_url, find_ffmpeg

try:
    FFMPEG: Path | None = find_ffmpeg()
except FFmpegNotFoundError:
    FFMPEG = None


def _ffmpeg(*args: str) -> subprocess.CompletedProcess:
    assert FFMPEG is not None
    return subprocess.run(
        [str(FFMPEG), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args],
        capture_output=True,
        check=True,
    )


def make_mp4(path: Path, *, seconds: float = 2, size: str = "640x360", audio: bool = True) -> Path:
    args = ["-f", "lavfi", "-i", f"testsrc=size={size}:rate=25:duration={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac", "-shortest"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-f", "mp4", file_url(path)]
    _ffmpeg(*args)
    return path


def make_gif(path: Path, *, seconds: float = 1, size: str = "121x75") -> Path:
    """An odd-sized GIF, to check that GIF -> MP4 handles odd dimensions."""
    _ffmpeg("-f", "lavfi", "-i", f"testsrc=size={size}:rate=10:duration={seconds}", "-f", "gif", file_url(path))
    return path


_VIDEO_RE = re.compile(r"Stream #\S+: Video: (\w+).*?, (\d+)x(\d+)")
_AUDIO_RE = re.compile(r"Stream #\S+: Audio: (\w+)")


def probe(path: Path) -> dict:
    """Return the codecs and size FFmpeg sees in a file."""
    assert FFMPEG is not None
    result = subprocess.run(
        [str(FFMPEG), "-hide_banner", "-nostdin", "-i", file_url(path)],
        capture_output=True,
    )
    text = result.stderr.decode("utf-8", errors="replace")
    info: dict = {"video": None, "audio": None, "width": None, "height": None}
    if match := _VIDEO_RE.search(text):
        info["video"], info["width"], info["height"] = match.group(1), int(match.group(2)), int(match.group(3))
    if match := _AUDIO_RE.search(text):
        info["audio"] = match.group(1)
    return info
