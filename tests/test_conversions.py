import os
import unittest
from pathlib import Path

from clickvert.conversions import CONVERSIONS, GIF_FPS, GIF_MAX_WIDTH, conversions_for, get_conversion
from clickvert.errors import InvalidInputError

ROOT = Path(os.path.abspath("videos"))  # only used to build names; never touched


class ConversionTableTests(unittest.TestCase):
    def test_exactly_the_v1_conversions(self):
        pairs = {(c.source, c.target) for c in CONVERSIONS}
        self.assertEqual(pairs, {("mp4", "gif"), ("mp4", "mp3"), ("gif", "mp4")})

    def test_gif_defaults(self):
        self.assertEqual((GIF_FPS, GIF_MAX_WIDTH), (12, 480))

    def test_lookup_is_case_insensitive(self):
        self.assertEqual(get_conversion(ROOT / "CLIP.MP4", "GIF").target, "gif")
        self.assertEqual(get_conversion(ROOT / "clip.gif", ".mp4").target, "mp4")

    def test_options_by_extension(self):
        self.assertEqual([c.target for c in conversions_for(ROOT / "a.mp4")], ["gif", "mp3"])
        self.assertEqual([c.target for c in conversions_for(ROOT / "a.gif")], ["mp4"])
        self.assertEqual(conversions_for(ROOT / "a.mp3"), [])

    def test_unsupported_type(self):
        with self.assertRaises(InvalidInputError) as ctx:
            get_conversion(ROOT / "notes.txt", "gif")
        self.assertIn("notes.txt", ctx.exception.user_message)

    def test_unsupported_target(self):
        with self.assertRaises(InvalidInputError) as ctx:
            get_conversion(ROOT / "anim.gif", "mp3")
        self.assertIn("Available: MP4", ctx.exception.user_message)


class PassArgumentTests(unittest.TestCase):
    """Every pass must name its output format explicitly and use file: URLs."""

    def test_every_pass_is_safe(self):
        for conversion in CONVERSIONS:
            src = ROOT / f"-tricky name.{conversion.source}"
            out = ROOT / f"x.clickvert-tmp-0{conversion.target_ext}"
            passes = conversion.build(src, out, ROOT / "scratch")
            with self.subTest(conversion=conversion.label):
                self.assertAlmostEqual(sum(p.weight for p in passes), 1.0)
                for p in passes:
                    args = list(p.args)
                    self.assertIn("-f", args, "output format must be explicit")
                    inputs = [args[i + 1] for i, a in enumerate(args) if a == "-i"]
                    self.assertTrue(inputs)
                    for value in inputs:
                        self.assertTrue(value.startswith("file:"), "inputs must be file: URLs")
                    self.assertTrue(args[-1].startswith("file:"), "output must be a file: URL")
                self.assertEqual(passes[-1].args[-1], f"file:{out}")
                self.assertEqual(passes[-1].args[-2], conversion.target)


if __name__ == "__main__":
    unittest.main()
