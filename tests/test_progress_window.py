"""Tests for the progress window.

Most tests use FakeJob, a stand-in for ConversionJob that the test controls
step by step. Two tests run real conversions with FFmpeg. The window is kept
hidden (withdrawn) so tests don't flash windows on screen.
"""

import shutil
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

from clickvert import cli
from clickvert.converter import ConversionJob
from clickvert.errors import ConversionCancelledError, NoAudioStreamError
from clickvert.paths import TEMP_MARKER
from clickvert.progress_window import ProgressWindow, apply_style, shorten_middle

from .media import FFMPEG, make_mp4


class FakeJob:
    """Behaves like ConversionJob, but the test decides what happens and when."""

    def __init__(self, input_path, target, *, on_progress=None):
        self.on_progress = on_progress
        self.finish = threading.Event()
        self.result = Path(input_path).with_suffix(f".{target}")
        self.error = None
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    @property
    def cancel_requested(self):
        return self._cancel.is_set()

    def run(self):
        while not self.finish.wait(0.01):
            if self._cancel.is_set():
                time.sleep(0.05)  # like FFmpeg taking a moment to stop
                raise ConversionCancelledError("Conversion cancelled.")
        if self.error:
            raise self.error
        return self.result


def tk_available():
    try:
        tk.Tk().destroy()
        return True
    except tk.TclError:
        return False


@unittest.skipUnless(tk_available(), "no display available for tkinter")
class WindowTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        apply_style(self.root)
        self.jobs = []
        self.windows = []

    def tearDown(self):
        # Shut down like the real app: let the conversion end, then close.
        for job in self.jobs:
            job.finish.set()
        for window in self.windows:
            self.pump_until(lambda: window.finished or self.window_closed())
        if not self.window_closed():
            self.root.destroy()

    def fake_factory(self, *args, **kwargs):
        job = FakeJob(*args, **kwargs)
        self.jobs.append(job)
        return job

    def open_window(self, path=r"C:\Videos\My Holiday.mp4", target="gif", **kwargs):
        kwargs.setdefault("job_factory", self.fake_factory)
        window = ProgressWindow(self.root, path, target, **kwargs)
        window.start()
        self.windows.append(window)
        return window

    def pump_until(self, condition, timeout=30):
        """Run the window's event loop until condition() is true."""
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > deadline:
                self.fail("timed out waiting for the window")
            try:
                self.root.update()
            except tk.TclError:  # window was destroyed
                if not condition():
                    raise
            time.sleep(0.005)

    def window_closed(self):
        try:
            return not self.root.winfo_exists()
        except tk.TclError:
            return True

    def text(self, widget):
        return str(widget.cget("text"))


class ProgressWindowTests(WindowTestCase):
    def test_shows_file_name_and_target_format(self):
        window = self.open_window()
        self.assertEqual(self.text(window.heading), "Converting to GIF")
        self.assertEqual(self.text(window.detail), "My Holiday.mp4")
        self.assertEqual(self.text(window.primary), "Cancel")

    def test_progress_updates_bar_and_percentage(self):
        window = self.open_window()
        self.jobs[0].on_progress(0.42)
        self.pump_until(lambda: self.text(window.percent) == "42%")
        self.assertAlmostEqual(float(window.bar["value"]), 42.0)
        self.assertEqual(str(window.bar["mode"]), "determinate")

    def test_unknown_progress_shows_moving_bar(self):
        window = self.open_window()
        self.jobs[0].on_progress(None)
        self.pump_until(lambda: str(window.bar["mode"]) == "indeterminate")
        self.assertEqual(self.text(window.percent), "Working…")

    def test_stays_responsive_while_converting(self):
        window = self.open_window()
        ticks = []
        for i in range(5):
            self.root.after(10 * i, lambda: ticks.append(1))
        self.pump_until(lambda: len(ticks) == 5, timeout=2)
        self.assertFalse(window.finished)

    def test_success_shows_where_file_was_saved(self):
        window = self.open_window()
        self.jobs[0].finish.set()
        self.pump_until(lambda: window.finished)

        self.assertEqual(window.exit_code, 0)
        self.assertEqual(self.text(window.heading), "Done")
        self.assertIn("Saved as My Holiday.gif", self.text(window.detail))
        self.assertIn(r"C:\Videos", self.text(window.detail))
        self.assertEqual(self.text(window.percent), "100%")
        self.assertEqual(self.text(window.secondary), "Show in folder")
        self.assertEqual(self.text(window.primary), "Close")

    def test_show_in_folder_opens_explorer_with_file_selected(self):
        window = self.open_window()
        self.jobs[0].finish.set()
        self.pump_until(lambda: window.finished)
        with mock.patch("clickvert.progress_window.subprocess.Popen") as popen:
            window.secondary.invoke()
        popen.assert_called_once_with(r'explorer /select,"C:\Videos\My Holiday.gif"')

    def test_error_shows_plain_message(self):
        window = self.open_window(target="mp3")
        self.jobs[0].error = NoAudioStreamError("“My Holiday.mp4” has no audio track.")
        self.jobs[0].finish.set()
        self.pump_until(lambda: window.finished)

        self.assertEqual(window.exit_code, NoAudioStreamError.exit_code)
        self.assertEqual(self.text(window.heading), "Couldn't convert")
        self.assertEqual(self.text(window.detail), "“My Holiday.mp4” has no audio track.")
        self.assertEqual(self.text(window.primary), "Close")
        self.assertFalse(window.bar.winfo_ismapped())

    def test_unexpected_crash_is_reported_not_hidden(self):
        window = self.open_window()
        self.jobs[0].error = RuntimeError("bug!")
        self.jobs[0].finish.set()
        self.pump_until(lambda: window.finished)
        self.assertEqual(window.exit_code, 1)
        self.assertIn("Something unexpected went wrong", self.text(window.detail))

    def test_cancel_button_stops_conversion_and_closes(self):
        window = self.open_window()
        window.primary.invoke()

        self.assertTrue(self.jobs[0].cancel_requested)
        self.assertEqual(self.text(window.heading), "Cancelling…")
        self.assertIn("disabled", window.primary.state())
        self.pump_until(self.window_closed)
        self.assertEqual(window.exit_code, ConversionCancelledError.exit_code)

    def test_closing_window_mid_conversion_cancels_first(self):
        window = self.open_window()
        window.close()  # same as clicking the window's X
        self.assertTrue(self.jobs[0].cancel_requested)
        self.assertFalse(self.window_closed(), "closed before cleanup finished")
        self.pump_until(self.window_closed)
        self.assertEqual(window.exit_code, ConversionCancelledError.exit_code)

    def test_close_after_done(self):
        window = self.open_window()
        self.jobs[0].finish.set()
        self.pump_until(lambda: window.finished)
        window.primary.invoke()
        self.assertTrue(self.window_closed())


class ShortenMiddleTests(unittest.TestCase):
    def test_short_names_unchanged(self):
        self.assertEqual(shorten_middle("clip.mp4"), "clip.mp4")

    def test_long_names_keep_start_and_extension(self):
        result = shorten_middle("a" * 80 + ".mp4", limit=20)
        self.assertEqual(len(result), 20)
        self.assertTrue(result.startswith("aaaa"))
        self.assertTrue(result.endswith(".mp4"))
        self.assertIn("…", result)


class CliWindowFlagTests(unittest.TestCase):
    def test_window_flag_opens_progress_window(self):
        with mock.patch("clickvert.progress_window.run_window", return_value=0) as run_window:
            code = cli.main(["convert", "--to", "gif", "--window", "clip.mp4"])
        self.assertEqual(code, 0)
        run_window.assert_called_once_with("clip.mp4", "gif")


@unittest.skipIf(FFMPEG is None, "FFmpeg is not installed")
class RealConversionWindowTests(WindowTestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        super().tearDown()
        self._tmp.cleanup()

    def test_real_conversion_reaches_done(self):
        source = make_mp4(self.dir / "clip.mp4", seconds=1)
        window = self.open_window(source, "mp3", job_factory=ConversionJob)
        self.pump_until(lambda: window.finished)
        self.assertEqual(window.exit_code, 0)
        self.assertEqual(window.output_path, self.dir / "clip.mp3")
        self.assertTrue(window.output_path.is_file())

    def test_real_cancel_leaves_no_partial_file(self):
        source = make_mp4(self.dir / "long.mp4", seconds=60, size="1280x720")
        window = self.open_window(source, "gif", job_factory=ConversionJob)
        self.pump_until(lambda: self.text(window.percent).endswith("%") and self.text(window.percent) != "0%")
        window.primary.invoke()
        self.pump_until(self.window_closed)

        self.assertEqual(window.exit_code, ConversionCancelledError.exit_code)
        self.assertEqual([p.name for p in self.dir.iterdir()], ["long.mp4"])
        self.assertFalse(any(TEMP_MARKER in p.name for p in self.dir.iterdir()))


if __name__ == "__main__":
    unittest.main()
