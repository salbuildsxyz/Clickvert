"""The table of supported conversions and the FFmpeg settings for each.

To add a conversion later, write a ``_build_*`` function and add one
``Conversion(...)`` entry to ``CONVERSIONS``. Nothing else needs to change.

A conversion is made of one or more *passes* (FFmpeg runs). Each pass has a
``weight``: its share of the progress bar.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .errors import InvalidInputError
from .ffmpeg import file_url

GIF_FPS = 12
GIF_MAX_WIDTH = 480


@dataclass(frozen=True)
class Pass:
    args: tuple[str, ...]
    weight: float


# build(input_path, temp_output_path, scratch_folder) -> passes
PassBuilder = Callable[[Path, Path, Path], list[Pass]]


@dataclass(frozen=True)
class Conversion:
    source: str  # "mp4"
    target: str  # "gif"
    label: str  # shown in menus: "Convert to GIF"
    build: PassBuilder

    @property
    def source_ext(self) -> str:
        return f".{self.source}"

    @property
    def target_ext(self) -> str:
        return f".{self.target}"


def _gif_filters() -> str:
    # Lower the frame rate, then shrink to at most 480 px wide (never enlarge).
    # Height -1 keeps the aspect ratio. Lanczos gives sharp downscaling.
    return f"fps={GIF_FPS},scale='min({GIF_MAX_WIDTH},iw)':-1:flags=lanczos"


def _build_mp4_to_gif(src: Path, out: Path, scratch: Path) -> list[Pass]:
    # GIFs can only use 256 colors. Pass 1 studies the video and picks the best
    # 256 colors (a "palette"); pass 2 draws the GIF using that palette. This
    # looks far better than FFmpeg's generic palette.
    palette = scratch / "palette.png"
    find_palette = Pass(
        args=(
            "-i", file_url(src),
            "-vf", f"{_gif_filters()},palettegen=stats_mode=diff",
            "-update", "1", "-frames:v", "1",
            "-f", "image2", file_url(palette),
        ),
        weight=0.4,
    )
    draw_gif = Pass(
        args=(
            "-i", file_url(src),
            "-i", file_url(palette),
            "-lavfi", f"{_gif_filters()}[v];[v][1:v]paletteuse=dither=sierra2_4a",
            "-loop", "0",  # loop forever, like most GIFs
            "-f", "gif", file_url(out),
        ),
        weight=0.6,
    )
    return [find_palette, draw_gif]


def _build_mp4_to_mp3(src: Path, out: Path, scratch: Path) -> list[Pass]:
    return [
        Pass(
            args=(
                "-i", file_url(src),
                "-map", "0:a:0",  # first audio track; fails clearly if there is none
                "-vn",
                "-c:a", "libmp3lame", "-q:a", "2",  # high-quality variable bitrate (~190 kbps)
                "-id3v2_version", "3",  # tag version Windows Explorer reads best
                "-f", "mp3", file_url(out),
            ),
            weight=1.0,
        )
    ]


def _build_gif_to_mp4(src: Path, out: Path, scratch: Path) -> list[Pass]:
    return [
        Pass(
            args=(
                "-i", file_url(src),
                # H.264 needs even width and height: add a 1 px border if needed.
                "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
                "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                "-pix_fmt", "yuv420p",  # the color format every player supports
                "-movflags", "+faststart",  # can start playing before fully downloaded
                "-an",
                "-f", "mp4", file_url(out),
            ),
            weight=1.0,
        )
    ]


CONVERSIONS: tuple[Conversion, ...] = (
    Conversion("mp4", "gif", "Convert to GIF", _build_mp4_to_gif),
    Conversion("mp4", "mp3", "Convert to MP3", _build_mp4_to_mp3),
    Conversion("gif", "mp4", "Convert to MP4", _build_gif_to_mp4),
)


def conversions_for(path: Path) -> list[Conversion]:
    """All conversions available for a file, based on its extension."""
    ext = path.suffix.lower()
    return [c for c in CONVERSIONS if c.source_ext == ext]


def get_conversion(path: Path, target: str) -> Conversion:
    target = target.lower().lstrip(".")
    options = conversions_for(path)
    for conversion in options:
        if conversion.target == target:
            return conversion
    if not options:
        supported = ", ".join(sorted({c.source.upper() for c in CONVERSIONS}))
        raise InvalidInputError(
            f"Clickvert can't convert “{path.name}”. Supported file types: {supported}."
        )
    choices = ", ".join(c.target.upper() for c in options)
    raise InvalidInputError(
        f"“{path.name}” can't be converted to {target.upper()}. Available: {choices}."
    )
