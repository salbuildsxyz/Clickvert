import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from clickvert import ffmpeg
from clickvert.errors import FFmpegNotFoundError


class FindFFmpegTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.fake = self.dir / ffmpeg.EXE_NAME
        self.fake.write_bytes(b"")

    def tearDown(self):
        self._tmp.cleanup()

    def test_env_override_wins(self):
        with mock.patch.dict(os.environ, {ffmpeg.FFMPEG_ENV_VAR: str(self.fake), "PATH": ""}):
            self.assertEqual(ffmpeg.find_ffmpeg(), self.fake)

    def test_env_override_must_exist(self):
        with mock.patch.dict(os.environ, {ffmpeg.FFMPEG_ENV_VAR: str(self.dir / "missing.exe")}):
            with self.assertRaises(FFmpegNotFoundError):
                ffmpeg.find_ffmpeg()

    def test_env_override_must_be_absolute(self):
        with mock.patch.dict(os.environ, {ffmpeg.FFMPEG_ENV_VAR: ffmpeg.EXE_NAME}):
            with self.assertRaises(FFmpegNotFoundError):
                ffmpeg.find_ffmpeg()

    def test_found_on_path(self):
        path_value = os.pathsep.join(["", str(self.dir / "empty"), str(self.dir)])
        self.assertEqual(ffmpeg.search_path(path_value), self.fake)

    def test_relative_path_entries_are_ignored(self):
        """A stray ffmpeg.exe in the current folder must never be picked up."""
        cwd = os.getcwd()
        os.chdir(self.dir)
        try:
            self.assertIsNone(ffmpeg.search_path(os.pathsep.join([".", "", "subdir"])))
        finally:
            os.chdir(cwd)

    def test_missing_everywhere(self):
        env = {k: v for k, v in os.environ.items() if k != ffmpeg.FFMPEG_ENV_VAR}
        env["PATH"] = str(self.dir / "empty")
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(FFmpegNotFoundError) as ctx:
                ffmpeg.find_ffmpeg()
        self.assertIn("winget install Gyan.FFmpeg", ctx.exception.user_message)


class ParsingTests(unittest.TestCase):
    def test_parse_duration(self):
        line = "  Duration: 01:02:03.50, start: 0.000000, bitrate: 1205 kb/s"
        self.assertAlmostEqual(ffmpeg.parse_duration(line), 3723.5)

    def test_parse_duration_not_available(self):
        self.assertIsNone(ffmpeg.parse_duration("  Duration: N/A, bitrate: N/A"))

    def test_parse_progress(self):
        self.assertAlmostEqual(ffmpeg.parse_progress_line("out_time_us=2500000\n"), 2.5)
        self.assertIsNone(ffmpeg.parse_progress_line("out_time_us=N/A"))
        self.assertIsNone(ffmpeg.parse_progress_line("frame=12"))

    def test_file_url(self):
        path = Path(os.path.abspath("-tricky & name.mp4"))
        self.assertEqual(ffmpeg.file_url(path), f"file:{path}")

    def test_file_url_rejects_relative(self):
        with self.assertRaises(ValueError):
            ffmpeg.file_url(Path("relative.mp4"))


if __name__ == "__main__":
    unittest.main()
