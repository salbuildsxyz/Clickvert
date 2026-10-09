"""Errors that Clickvert reports.

Every error has two parts:

* ``user_message``: a short, plain-language sentence meant for a person.
  It will appear in the progress window or a message box.
* ``details``: technical information (FFmpeg output, the exact command, ...)
  meant for the log file and for bug reports.

Each error type also has an ``exit_code`` so the command line tool and the
tests can tell failures apart.
"""

from __future__ import annotations


class ClickvertError(Exception):
    exit_code = 1

    def __init__(self, user_message: str, details: str = "") -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.details = details


class InvalidInputError(ClickvertError):
    """The file can't be converted: it's missing, unreadable, or unsupported."""

    exit_code = 3


class FFmpegNotFoundError(ClickvertError):
    exit_code = 4


class ConversionFailedError(ClickvertError):
    """FFmpeg ran but couldn't produce the output."""

    exit_code = 5


class NoAudioStreamError(ConversionFailedError):
    """Asked for audio (MP3) from a video that has no audio track."""


class ConversionCancelledError(ClickvertError):
    exit_code = 6


class IntegrationError(ClickvertError):
    """Installing or removing the right-click menu failed."""

    exit_code = 7
