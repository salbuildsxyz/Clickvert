import os
import tempfile
import threading
import unittest
from pathlib import Path

from clickvert.errors import InvalidInputError
from clickvert.paths import (
    TEMP_MARKER,
    candidate_output_names,
    create_temp_output,
    finalize_output,
    remove_quietly,
    validate_input,
)


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def make_file(self, name, content=b"data"):
        path = self.dir / name
        path.write_bytes(content)
        return path


class ValidateInputTests(TempDirTestCase):
    def test_returns_absolute_path(self):
        path = self.make_file("clip.mp4")
        cwd = os.getcwd()
        os.chdir(self.dir)
        try:
            self.assertEqual(validate_input("clip.mp4"), path)
        finally:
            os.chdir(cwd)

    def test_missing_file(self):
        with self.assertRaises(InvalidInputError) as ctx:
            validate_input(self.dir / "nope.mp4")
        self.assertIn("nope.mp4", ctx.exception.user_message)

    def test_folder_is_rejected(self):
        folder = self.dir / "looks-like-a.mp4"
        folder.mkdir()
        with self.assertRaises(InvalidInputError):
            validate_input(folder)

    def test_empty_path(self):
        with self.assertRaises(InvalidInputError):
            validate_input("  ")

    def test_unusual_names_are_accepted(self):
        for name in ["café 🎬 & 'quotes' (final) 50%.mp4", "-starts-with-dash.mp4", "名前.gif"]:
            with self.subTest(name=name):
                path = self.make_file(name)
                self.assertEqual(validate_input(path), path)


class TempOutputTests(TempDirTestCase):
    def test_temp_file_keeps_target_extension(self):
        source = self.make_file("clip.mp4")
        temp = create_temp_output(source, ".gif")
        self.assertTrue(temp.exists())
        self.assertEqual(temp.suffix, ".gif")
        self.assertEqual(temp.parent, self.dir)
        self.assertIn(TEMP_MARKER, temp.name)

    def test_temp_files_are_unique(self):
        source = self.make_file("clip.mp4")
        temps = {create_temp_output(source, ".gif") for _ in range(50)}
        self.assertEqual(len(temps), 50)

    def test_very_long_stem_is_shortened(self):
        source = self.make_file("x" * 200 + ".mp4")
        temp = create_temp_output(source, ".gif")
        self.assertLess(len(temp.name), 150)


class OutputNamingTests(TempDirTestCase):
    def test_candidate_sequence(self):
        names = [p.name for _, p in zip(range(3), candidate_output_names(self.dir / "a b.mp4", ".gif"))]
        self.assertEqual(names, ["a b.gif", "a b (1).gif", "a b (2).gif"])

    def test_uses_plain_name_when_free(self):
        source = self.make_file("clip.mp4")
        temp = self.make_file("clip.clickvert-tmp-0000.gif", b"new")
        final = finalize_output(temp, source, ".gif")
        self.assertEqual(final.name, "clip.gif")
        self.assertEqual(final.read_bytes(), b"new")
        self.assertFalse(temp.exists())

    def test_never_overwrites_existing_files(self):
        source = self.make_file("clip.mp4")
        self.make_file("clip.gif", b"keep me")
        self.make_file("clip (1).gif", b"keep me too")
        temp = self.make_file("clip.clickvert-tmp-0000.gif", b"new")

        final = finalize_output(temp, source, ".gif")

        self.assertEqual(final.name, "clip (2).gif")
        self.assertEqual((self.dir / "clip.gif").read_bytes(), b"keep me")
        self.assertEqual((self.dir / "clip (1).gif").read_bytes(), b"keep me too")

    def test_skips_name_taken_by_a_folder(self):
        source = self.make_file("clip.mp4")
        (self.dir / "clip.gif").mkdir()
        temp = self.make_file("clip.clickvert-tmp-0000.gif")
        self.assertEqual(finalize_output(temp, source, ".gif").name, "clip (1).gif")

    def test_simultaneous_finalizes_never_collide(self):
        """20 threads finish at once. Every result must land under its own name."""
        source = self.make_file("clip.mp4")
        temps = [self.make_file(f"t{i}{TEMP_MARKER}{i}.gif", f"output {i}".encode()) for i in range(20)]
        barrier = threading.Barrier(len(temps))
        results = {}

        def worker(i, temp):
            barrier.wait()
            results[i] = finalize_output(temp, source, ".gif")

        threads = [threading.Thread(target=worker, args=(i, t)) for i, t in enumerate(temps)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        finals = list(results.values())
        self.assertEqual(len(set(finals)), 20, "two conversions got the same name")
        for i, final in results.items():
            self.assertEqual(final.read_bytes(), f"output {i}".encode(), "an output was overwritten")


class RemoveQuietlyTests(TempDirTestCase):
    def test_removes_file(self):
        path = self.make_file("x.gif")
        self.assertTrue(remove_quietly(path))
        self.assertFalse(path.exists())

    def test_missing_file_is_fine(self):
        self.assertTrue(remove_quietly(self.dir / "never-existed.gif"))


if __name__ == "__main__":
    unittest.main()
