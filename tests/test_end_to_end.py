"""Real conversions with real FFmpeg. Skipped if FFmpeg isn't installed."""

import _thread
import hashlib
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from clickvert.converter import ConversionJob
from clickvert.errors import (
    ConversionCancelledError,
    ConversionFailedError,
    InvalidInputError,
    NoAudioStreamError,
)
from clickvert.paths import TEMP_MARKER

from .media import FFMPEG, make_gif, make_mp4, probe


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipIf(FFMPEG is None, "FFmpeg is not installed")
class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._samples = tempfile.TemporaryDirectory()
        samples = Path(cls._samples.name)
        cls.sample_mp4 = make_mp4(samples / "sample.mp4")
        cls.small_mp4 = make_mp4(samples / "small.mp4", size="320x240")
        cls.silent_mp4 = make_mp4(samples / "silent.mp4", audio=False)
        cls.sample_gif = make_gif(samples / "sample.gif")
        cls.long_mp4 = make_mp4(samples / "long.mp4", seconds=60, size="1280x720")

    @classmethod
    def tearDownClass(cls):
        cls._samples.cleanup()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def copy_in(self, sample: Path, name: str) -> Path:
        dest = self.dir / name
        shutil.copyfile(sample, dest)
        return dest

    def assert_no_temp_files(self):
        leftovers = [p.name for p in self.dir.iterdir() if TEMP_MARKER in p.name]
        self.assertEqual(leftovers, [], "partial output was left behind")

    # --- the three conversions ---------------------------------------------

    def test_mp4_to_gif(self):
        source = self.copy_in(self.sample_mp4, "clip.mp4")
        before = sha256(source)
        progress = []

        output = ConversionJob(source, "gif", on_progress=progress.append).run()

        self.assertEqual(output, self.dir / "clip.gif")
        self.assertEqual(output.read_bytes()[:6], b"GIF89a")
        info = probe(output)
        self.assertEqual((info["video"], info["width"], info["height"]), ("gif", 480, 270))
        self.assertEqual(sha256(source), before, "original file was modified")
        self.assertEqual(progress[-1], 1.0)
        numbers = [p for p in progress if p is not None]
        self.assertEqual(numbers, sorted(numbers), "progress went backwards")
        self.assert_no_temp_files()

    def test_small_video_is_not_enlarged(self):
        source = self.copy_in(self.small_mp4, "small.mp4")
        output = ConversionJob(source, "gif").run()
        self.assertEqual(probe(output)["width"], 320)

    def test_mp4_to_mp3(self):
        source = self.copy_in(self.sample_mp4, "song.mp4")
        output = ConversionJob(source, "mp3").run()
        self.assertEqual(output.name, "song.mp3")
        info = probe(output)
        self.assertEqual((info["audio"], info["video"]), ("mp3", None))
        self.assert_no_temp_files()

    def test_gif_to_mp4_with_odd_dimensions(self):
        source = self.copy_in(self.sample_gif, "anim.gif")
        output = ConversionJob(source, "mp4").run()
        self.assertEqual(output.name, "anim.mp4")
        self.assertEqual(output.read_bytes()[4:8], b"ftyp")
        info = probe(output)
        self.assertEqual((info["video"], info["width"], info["height"]), ("h264", 122, 76))
        self.assert_no_temp_files()

    # --- file names ----------------------------------------------------------

    def test_unusual_file_names(self):
        for name in ["café 🎬 & 'quotes' (final) 50%.mp4", "-starts-with-dash.mp4", "名前.mp4"]:
            with self.subTest(name=name):
                source = self.copy_in(self.sample_mp4, name)
                output = ConversionJob(source, "mp3").run()
                self.assertEqual(output, source.with_suffix(".mp3"))
                self.assertTrue(output.is_file())

    def test_uppercase_extension(self):
        source = self.copy_in(self.sample_gif, "LOUD.GIF")
        self.assertEqual(ConversionJob(source, "mp4").run().name, "LOUD.mp4")

    def test_existing_output_is_kept(self):
        source = self.copy_in(self.sample_mp4, "clip.mp4")
        (self.dir / "clip.mp3").write_bytes(b"precious")
        output = ConversionJob(source, "mp3").run()
        self.assertEqual(output.name, "clip (1).mp3")
        self.assertEqual((self.dir / "clip.mp3").read_bytes(), b"precious")

    def test_simultaneous_conversions_get_separate_names(self):
        source = self.copy_in(self.sample_mp4, "clip.mp4")
        barrier = threading.Barrier(4)
        outputs, errors = [], []

        def worker():
            barrier.wait()
            try:
                outputs.append(ConversionJob(source, "mp3").run())
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(
            sorted(p.name for p in outputs),
            ["clip (1).mp3", "clip (2).mp3", "clip (3).mp3", "clip.mp3"],
        )
        self.assert_no_temp_files()

    # --- errors and cancellation ----------------------------------------------

    def test_video_without_audio_to_mp3(self):
        source = self.copy_in(self.silent_mp4, "silent.mp4")
        with self.assertRaises(NoAudioStreamError) as ctx:
            ConversionJob(source, "mp3").run()
        self.assertIn("no audio track", ctx.exception.user_message)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["silent.mp4"])

    def test_damaged_file(self):
        source = self.dir / "broken.mp4"
        source.write_bytes(b"this is not a video")
        with self.assertRaises(ConversionFailedError) as ctx:
            ConversionJob(source, "gif").run()
        self.assertIn("broken.mp4", ctx.exception.user_message)
        self.assertIn("Command:", ctx.exception.details)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["broken.mp4"])

    def test_unsupported_conversion_creates_nothing(self):
        source = self.copy_in(self.sample_gif, "anim.gif")
        with self.assertRaises(InvalidInputError):
            ConversionJob(source, "mp3").run()
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["anim.gif"])

    def test_cancel_during_conversion_cleans_up(self):
        source = self.copy_in(self.long_mp4, "long.mp4")
        job = None

        def on_progress(fraction):
            if fraction is not None and 0 < fraction < 1:
                job.cancel()

        job = ConversionJob(source, "gif", on_progress=on_progress)
        with self.assertRaises(ConversionCancelledError):
            job.run()
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["long.mp4"])

    def test_cancel_from_another_thread(self):
        """Like pressing a Cancel button while FFmpeg is busy."""
        source = self.copy_in(self.long_mp4, "long.mp4")
        started = threading.Event()
        job = ConversionJob(source, "gif", on_progress=lambda f: started.set())
        result = []

        thread = threading.Thread(target=lambda: result.append(self._run_capturing(job)))
        thread.start()
        self.assertTrue(started.wait(timeout=30), "conversion never reported progress")
        job.cancel()
        thread.join(timeout=10)

        self.assertFalse(thread.is_alive(), "cancel did not stop the conversion")
        self.assertIsInstance(result[0], ConversionCancelledError)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["long.mp4"])

    def test_ctrl_c_stops_ffmpeg_and_cleans_up(self):
        """A real KeyboardInterrupt in the main thread, as Ctrl+C would cause."""
        source = self.copy_in(self.long_mp4, "long.mp4")
        interrupted = threading.Event()

        def on_progress(fraction):
            if fraction is not None and 0 < fraction < 1 and not interrupted.is_set():
                interrupted.set()
                _thread.interrupt_main()

        with self.assertRaises(KeyboardInterrupt):
            ConversionJob(source, "gif", on_progress=on_progress).run()
        # If FFmpeg were still running, Windows would have refused to delete its file.
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["long.mp4"])

    def test_cancel_before_start(self):
        source = self.copy_in(self.sample_mp4, "clip.mp4")
        job = ConversionJob(source, "gif")
        job.cancel()
        with self.assertRaises(ConversionCancelledError):
            job.run()
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["clip.mp4"])

    @staticmethod
    def _run_capturing(job):
        try:
            return job.run()
        except Exception as exc:
            return exc


if __name__ == "__main__":
    unittest.main()
