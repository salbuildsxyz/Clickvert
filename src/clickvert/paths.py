"""Safe file path handling.

This module makes three promises:

1. The original file is only ever *read*.
2. An existing file is never overwritten, even when several conversions of the
   same file finish at the same moment.
3. A half-written file never appears under the final name.

How it works:

* Before converting, we create a uniquely named, empty temporary file next to
  the original (``clip.clickvert-tmp-1a2b3c4d.gif``). It's created with
  "exclusive create", which fails rather than reuse a name that already exists,
  so two conversions can never share a temp file. The temp name keeps the real
  extension, so it is always a valid FFmpeg output name.
* FFmpeg writes into that temp file.
* Only when FFmpeg succeeds do we *rename* the temp file to its final name.
  On Windows, a rename refuses to replace an existing file, and the check and
  the rename happen as one step inside Windows. If ``clip.gif`` is taken (even
  if another conversion took it a millisecond ago), the rename fails and we try
  ``clip (1).gif``, then ``clip (2).gif``, and so on.
"""

from __future__ import annotations

import os
import secrets
import time
from collections.abc import Iterator
from pathlib import Path

from .errors import ConversionFailedError, InvalidInputError

TEMP_MARKER = ".clickvert-tmp-"
MAX_NAME_ATTEMPTS = 10_000
# Keeps temp names comfortably under the 255-character file name limit.
_TEMP_STEM_LIMIT = 100


def validate_input(raw_path: str | os.PathLike[str]) -> Path:
    """Check that ``raw_path`` is an existing, readable file and return its absolute path."""
    if not str(raw_path).strip():
        raise InvalidInputError("No file was given.")

    # abspath (not resolve) keeps the folder the user actually clicked in,
    # even if the file is a shortcut-like symlink to somewhere else.
    path = Path(os.path.abspath(raw_path))

    if not path.exists():
        raise InvalidInputError(
            f"“{path.name}” could not be found. It may have been moved, renamed, or deleted."
        )
    if not path.is_file():
        raise InvalidInputError(f"“{path.name}” is a folder, not a file.")
    try:
        with open(path, "rb"):
            pass
    except OSError as exc:
        raise InvalidInputError(
            f"Clickvert can't open “{path.name}”. It may be in use by another program, "
            "or you may not have permission to read it.",
            details=repr(exc),
        ) from exc
    return path


def create_temp_output(input_path: Path, target_ext: str) -> Path:
    """Create an empty, uniquely named temp file next to ``input_path`` and return it."""
    folder = input_path.parent
    stem = input_path.stem[:_TEMP_STEM_LIMIT]
    for _ in range(100):
        candidate = folder / f"{stem}{TEMP_MARKER}{secrets.token_hex(4)}{target_ext}"
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            continue
        except OSError as exc:
            raise InvalidInputError(
                f"Clickvert can't save files in “{folder}”. The folder may be read-only, "
                "full, or protected.",
                details=repr(exc),
            ) from exc
        os.close(fd)
        return candidate
    raise ConversionFailedError("Clickvert couldn't create a temporary file.")


def candidate_output_names(input_path: Path, target_ext: str) -> Iterator[Path]:
    """Yield ``clip.gif``, ``clip (1).gif``, ``clip (2).gif``, ..."""
    stem = input_path.stem
    yield input_path.with_name(f"{stem}{target_ext}")
    for n in range(1, MAX_NAME_ATTEMPTS):
        yield input_path.with_name(f"{stem} ({n}){target_ext}")


def finalize_output(temp_path: Path, input_path: Path, target_ext: str) -> Path:
    """Move the finished temp file to the first free output name, never overwriting."""
    input_key = os.path.normcase(input_path)
    for candidate in candidate_output_names(input_path, target_ext):
        if os.path.normcase(candidate) == input_key or os.path.lexists(candidate):
            continue
        try:
            _move_without_overwrite(temp_path, candidate)
        except FileExistsError:
            # Someone claimed this name between our check and our rename.
            continue
        return candidate
    raise ConversionFailedError(
        f"Clickvert couldn't find a free file name for the converted copy of “{input_path.name}”."
    )


def _move_without_overwrite(src: Path, dst: Path) -> None:
    if os.name == "nt":
        # Windows rename fails with FileExistsError if dst exists, atomically.
        os.rename(src, dst)
    else:
        # POSIX rename silently replaces dst; a hard link does not.
        os.link(src, dst)
        os.unlink(src)


def remove_quietly(path: Path, attempts: int = 5) -> bool:
    """Delete ``path`` if it exists. Return True if it's gone afterwards.

    Retries briefly because antivirus scanners or a just-exited FFmpeg can hold
    a file open for a moment on Windows.
    """
    for attempt in range(attempts):
        try:
            os.unlink(path)
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            if attempt + 1 < attempts:
                time.sleep(0.1 * (attempt + 1))
        except OSError:
            return False
    return not os.path.lexists(path)
