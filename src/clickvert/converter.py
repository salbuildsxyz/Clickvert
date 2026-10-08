"""Runs one conversion from start to finish.

This is the piece the command line (and, in Milestone 2, the progress window)
uses. The steps:

1. Check the input file and find the matching conversion recipe.
2. Find FFmpeg.
3. Create a unique temp file next to the original.
4. Run FFmpeg (one or more passes), reporting progress.
5. On success, move the temp file to a free final name.
   On failure or cancel, delete the temp file.

``ConversionJob.cancel()`` can be called from any thread (for example, a Cancel
button). The job stops FFmpeg within about 0.1 s and cleans up.
"""

from __future__ import annotations

import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from .conversions import Conversion, Pass, get_conversion
from .errors import ConversionCancelledError, ConversionFailedError, NoAudioStreamError
from .ffmpeg import FFmpegOutcome, find_ffmpeg, run_ffmpeg
from .paths import create_temp_output, finalize_output, remove_quietly, validate_input

# Called with a fraction from 0.0 to 1.0, or None while progress is unknown.
# Runs on a background thread: GUI code must hand it to the GUI thread.
ProgressCallback = Callable[[float | None], None]


class ConversionJob:
    def __init__(
        self,
        input_path: str | os.PathLike[str],
        target: str,
        *,
        ffmpeg_path: Path | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        self.input_path = input_path
        self.target = target
        self.ffmpeg_path = ffmpeg_path
        self.on_progress = on_progress
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    def run(self) -> Path:
        """Convert the file and return the path of the new file."""
        source = validate_input(self.input_path)
        conversion = get_conversion(source, self.target)
        ffmpeg = self.ffmpeg_path or find_ffmpeg()
        self._raise_if_cancelled()

        temp_output = create_temp_output(source, conversion.target_ext)
        try:
            with tempfile.TemporaryDirectory(prefix="clickvert-", ignore_cleanup_errors=True) as scratch:
                passes = conversion.build(source, temp_output, Path(scratch))
                self._run_passes(ffmpeg, passes, source, conversion)
            self._raise_if_cancelled()
            if temp_output.stat().st_size == 0:
                raise ConversionFailedError(
                    f"FFmpeg finished but produced an empty file from “{source.name}”."
                )
            final = finalize_output(temp_output, source, conversion.target_ext)
        except BaseException:
            # Failure, cancel, or Ctrl+C: never leave a partial file behind.
            remove_quietly(temp_output)
            raise

        self._report(1.0)
        return final

    def _run_passes(self, ffmpeg: Path, passes: list[Pass], source: Path, conversion: Conversion) -> None:
        total_weight = sum(p.weight for p in passes)
        done_weight = 0.0
        for ffmpeg_pass in passes:
            self._raise_if_cancelled()
            base, share = done_weight / total_weight, ffmpeg_pass.weight / total_weight

            def on_time(seconds: float | None, duration: float | None, base=base, share=share) -> None:
                if seconds is None or not duration:
                    self._report(None)
                else:
                    self._report(base + share * min(seconds / duration, 1.0))

            outcome = run_ffmpeg(ffmpeg, ffmpeg_pass.args, cancel_event=self._cancel_event, on_time=on_time)
            if outcome.cancelled:
                raise ConversionCancelledError("Conversion cancelled.")
            if outcome.returncode != 0:
                raise _explain_failure(outcome, source, conversion)
            done_weight += ffmpeg_pass.weight
            self._report(done_weight / total_weight)

    def _raise_if_cancelled(self) -> None:
        if self._cancel_event.is_set():
            raise ConversionCancelledError("Conversion cancelled.")

    def _report(self, fraction: float | None) -> None:
        if self.on_progress is not None:
            self.on_progress(fraction)


def _explain_failure(outcome: FFmpegOutcome, source: Path, conversion: Conversion) -> ConversionFailedError:
    """Turn FFmpeg's technical output into a message a person can act on."""
    details = outcome.describe()
    if conversion.target == "mp3" and any("matches no streams" in line for line in outcome.stderr_tail):
        return NoAudioStreamError(
            f"“{source.name}” has no audio track, so there's nothing to save as MP3.",
            details=details,
        )
    reason = outcome.last_error_line
    if len(reason) > 200:
        reason = reason[:197] + "..."
    message = (
        f"FFmpeg couldn't convert “{source.name}”. The file may be damaged, "
        f"or it may not really be a {conversion.source.upper()} file."
    )
    if reason:
        message += f"\n\nFFmpeg said: {reason}"
    return ConversionFailedError(message, details=details)
