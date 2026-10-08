import io
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from clickvert.cli import main

from .media import FFMPEG, make_mp4


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_formats(self):
        code, out, _ = run_cli("formats")
        self.assertEqual(code, 0)
        self.assertIn("MP4 -> GIF", out)

    def test_missing_file(self):
        code, _, err = run_cli("convert", "--to", "gif", str(self.dir / "missing.mp4"))
        self.assertEqual(code, 3)
        self.assertIn("could not be found", err)

    def test_emoji_name_with_old_console_encoding(self):
        """Printing an error about "🎬.mp4" must not crash on a cp1252 console."""
        raw = io.BytesIO()
        old_console = io.TextIOWrapper(raw, encoding="cp1252")
        with redirect_stdout(io.StringIO()), redirect_stderr(old_console):
            code = main(["convert", "--to", "gif", str(self.dir / "🎬.mp4")])
            old_console.flush()
        self.assertEqual(code, 3)
        self.assertIn(b"could not be found", raw.getvalue())

    def test_bad_target_is_a_usage_error(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            main(["convert", "--to", "webm", "x.mp4"])
        self.assertEqual(ctx.exception.code, 2)

    @unittest.skipIf(FFMPEG is None, "FFmpeg is not installed")
    def test_convert_prints_output_path(self):
        source = self.dir / "clip.mp4"
        make_mp4(source, seconds=1)
        code, out, _ = run_cli("convert", "--to", "mp3", "--quiet", str(source))
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), str(self.dir / "clip.mp3"))


if __name__ == "__main__":
    unittest.main()
