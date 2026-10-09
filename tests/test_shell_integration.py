"""Tests for the right-click menu integration.

These never touch the real Windows registry: they use FakeRegistry, an
in-memory stand-in, and patch the CLI to use it too.
"""

import ctypes
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from clickvert import cli, shell_integration as shell
from clickvert.errors import IntegrationError

from .media import FFMPEG, make_mp4

PYTHONW = Path(r"C:\Python312\pythonw.exe")
PACKAGE = Path(r"C:\Users\Sam\Clickvert\src\clickvert")
REAL_PACKAGE = Path(shell.__file__).resolve().parent
MP4_MENU = r"Software\Classes\SystemFileAssociations\.mp4\shell\Clickvert"
GIF_MENU = r"Software\Classes\SystemFileAssociations\.gif\shell\Clickvert"


class FakeRegistry:
    """A dictionary pretending to be HKEY_CURRENT_USER."""

    def __init__(self):
        self.keys: dict[str, dict[str, str]] = {}
        self.refreshed = 0

    def key_exists(self, path):
        return path.lower() in self.keys

    def set_values(self, path, values):
        # Like the real registry, creating a key creates its parents too.
        parts = path.split("\\")
        for i in range(1, len(parts) + 1):
            self.keys.setdefault("\\".join(parts[:i]).lower(), {})
        self.keys[path.lower()].update(values)

    def delete_tree(self, path):
        prefix = path.lower()
        for key in list(self.keys):
            if key == prefix or key.startswith(prefix + "\\"):
                del self.keys[key]

    def delete_if_empty(self, path):
        key = path.lower()
        has_children = any(k.startswith(key + "\\") for k in self.keys)
        if key in self.keys and not self.keys[key] and not has_children:
            del self.keys[key]

    def refresh_explorer(self):
        self.refreshed += 1

    def values(self, path):
        return self.keys[path.lower()]


def split_like_explorer(command: str) -> list[str]:
    """Split a command line exactly the way Windows does (CommandLineToArgvW)."""
    argc = ctypes.c_int()
    fn = ctypes.windll.shell32.CommandLineToArgvW
    fn.restype = ctypes.POINTER(ctypes.c_wchar_p)
    argv = fn(command, ctypes.byref(argc))
    try:
        return [argv[i] for i in range(argc.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(argv)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.keys = {k.path: k.values for k in shell.plan(PYTHONW, PACKAGE)}

    def test_only_working_formats_appear(self):
        self.assertEqual(
            sorted(self.keys),
            sorted([
                MP4_MENU, MP4_MENU + r"\shell\gif", MP4_MENU + r"\shell\gif\command",
                MP4_MENU + r"\shell\mp3", MP4_MENU + r"\shell\mp3\command",
                GIF_MENU, GIF_MENU + r"\shell\mp4", GIF_MENU + r"\shell\mp4\command",
            ]),
        )

    def test_submenu_and_labels(self):
        self.assertEqual(self.keys[MP4_MENU], {"MUIVerb": "Clickvert", "SubCommands": ""})
        self.assertEqual(self.keys[MP4_MENU + r"\shell\gif"], {"": "Convert to GIF"})
        self.assertEqual(self.keys[MP4_MENU + r"\shell\mp3"], {"": "Convert to MP3"})
        self.assertEqual(self.keys[GIF_MENU + r"\shell\mp4"], {"": "Convert to MP4"})

    def test_command_opens_progress_window(self):
        self.assertEqual(
            self.keys[MP4_MENU + r"\shell\gif\command"][""],
            r'"C:\Python312\pythonw.exe" "C:\Users\Sam\Clickvert\src\clickvert" '
            r'convert --to gif --window "%1"',
        )

    def test_every_key_is_inside_a_clickvert_menu_key(self):
        """So uninstalling the menu keys removes everything we wrote."""
        for path in self.keys:
            self.assertTrue(any(path == m or path.startswith(m + "\\") for m in shell.menu_keys()), path)

    def test_never_touches_default_programs_or_windows_menu_settings(self):
        for path in self.keys:
            self.assertTrue(path.startswith("Software\\Classes\\SystemFileAssociations\\"), path)
            self.assertNotIn("CLSID", path)

    def test_rejects_percent_in_paths(self):
        with self.assertRaises(IntegrationError):
            shell.plan(PYTHONW, Path(r"C:\100% done\src\clickvert"))

    def test_rejects_relative_paths(self):
        with self.assertRaises(IntegrationError):
            shell.plan(Path("pythonw.exe"), PACKAGE)

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_explorer_splits_command_into_the_right_arguments(self):
        command = self.keys[MP4_MENU + r"\shell\mp3\command"][""]
        clicked = r"C:\Users\Sam\Videos\café 🎬 & 'quotes' (final) 50%.mp4"
        argv = split_like_explorer(command.replace("%1", clicked))
        self.assertEqual(argv, [str(PYTHONW), str(PACKAGE), "convert", "--to", "mp3", "--window", clicked])


class InstallUninstallTests(unittest.TestCase):
    def setUp(self):
        self.registry = FakeRegistry()
        self.keys = shell.plan(PYTHONW, PACKAGE)

    def test_install_writes_the_plan(self):
        shell.install(self.registry, self.keys)
        for key in self.keys:
            self.assertEqual(self.registry.values(key.path), key.values)
        self.assertGreaterEqual(self.registry.refreshed, 1)

    def test_install_twice_is_harmless_and_removes_stale_entries(self):
        self.registry.set_values(MP4_MENU + r"\shell\webm", {"": "Convert to WEBM (old version)"})
        shell.install(self.registry, self.keys)
        shell.install(self.registry, self.keys)
        self.assertFalse(self.registry.key_exists(MP4_MENU + r"\shell\webm"))
        self.assertTrue(self.registry.key_exists(MP4_MENU + r"\shell\gif\command"))

    def test_uninstall_removes_only_clickvert(self):
        other_app = r"Software\Classes\SystemFileAssociations\.mp4\shell\OtherApp\command"
        self.registry.set_values(other_app, {"": "other.exe %1"})
        shell.install(self.registry, self.keys)

        removed = shell.uninstall(self.registry)

        self.assertEqual(removed, [MP4_MENU, GIF_MENU])
        self.assertFalse(any("clickvert" in k for k in self.registry.keys), "Clickvert keys left behind")
        self.assertEqual(self.registry.values(other_app), {"": "other.exe %1"})
        self.assertTrue(self.registry.key_exists(r"Software\Classes\SystemFileAssociations\.mp4\shell"),
                        "a key another app still uses was deleted")

    def test_uninstall_leaves_no_empty_keys_behind(self):
        shell.install(self.registry, self.keys)
        shell.uninstall(self.registry)
        self.assertFalse(any(".mp4" in k or ".gif" in k for k in self.registry.keys))

    def test_uninstall_keeps_file_type_key_that_has_other_values(self):
        mp4_key = r"Software\Classes\SystemFileAssociations\.mp4"
        self.registry.set_values(mp4_key, {"PerceivedType": "video"})
        shell.install(self.registry, self.keys)
        shell.uninstall(self.registry)
        self.assertEqual(self.registry.values(mp4_key), {"PerceivedType": "video"})

    def test_uninstall_when_not_installed(self):
        self.assertEqual(shell.uninstall(self.registry), [])

    def test_registry_errors_become_friendly_errors(self):
        self.registry.set_values = mock.Mock(side_effect=PermissionError(5, "Access is denied"))
        with self.assertRaises(IntegrationError) as ctx:
            shell.install(self.registry, self.keys)
        self.assertIn("refused", ctx.exception.user_message)


class CliTests(unittest.TestCase):
    """The CLI commands, wired to FakeRegistry instead of the real registry."""

    def setUp(self):
        self.registry = FakeRegistry()
        patcher = mock.patch.object(shell, "WindowsRegistry", return_value=self.registry)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_install_dry_run_changes_nothing(self):
        code, out, _ = self.run_cli("install", "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(self.registry.keys, {})
        self.assertIn(r"[HKEY_CURRENT_USER\Software\Classes\SystemFileAssociations\.mp4\shell\Clickvert]", out)
        self.assertIn("convert --to gif --window", out)
        self.assertIn("Dry run: nothing was changed", out)

    def test_install_then_uninstall(self):
        code, out, _ = self.run_cli("install")
        self.assertEqual(code, 0)
        self.assertTrue(self.registry.key_exists(MP4_MENU + r"\shell\mp3\command"))
        self.assertIn("Show more options", out)

        code, out, _ = self.run_cli("uninstall", "--dry-run")
        self.assertIn("Would delete", out)
        self.assertTrue(self.registry.key_exists(MP4_MENU), "dry run must not delete")

        code, out, _ = self.run_cli("uninstall")
        self.assertEqual(code, 0)
        self.assertIn("removed", out)
        self.assertFalse(any(".mp4" in k or ".gif" in k for k in self.registry.keys), "empty keys left behind")

    def test_uninstall_when_not_installed(self):
        code, out, _ = self.run_cli("uninstall")
        self.assertEqual(code, 0)
        self.assertIn("wasn't installed", out)


@unittest.skipUnless(os.name == "nt", "Windows only")
class RealCommandTests(unittest.TestCase):
    """Run the exact command the menu stores, minus the window, on a real file."""

    def test_folder_launch_works_without_pythonpath(self):
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        result = subprocess.run(
            [sys.executable, str(REAL_PACKAGE), "formats"],
            capture_output=True, text=True, cwd=tempfile.gettempdir(), env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MP4 -> GIF", result.stdout)

    @unittest.skipIf(FFMPEG is None, "FFmpeg is not installed")
    def test_menu_command_converts_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            clicked = make_mp4(Path(tmp) / "café 🎬 & 'quotes' 50%.mp4", seconds=1)
            pythonw, package_dir = shell.default_launcher()
            command = shell.launch_command(pythonw, package_dir, "mp3")

            argv = split_like_explorer(command.replace("%1", str(clicked)))
            argv[0] = sys.executable  # python.exe instead of pythonw.exe, so we see errors
            argv.remove("--window")  # no window during automated tests
            env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
            result = subprocess.run(argv + ["--quiet"], capture_output=True, cwd=tmp, env=env)

            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            self.assertTrue(clicked.with_suffix(".mp3").is_file())


if __name__ == "__main__":
    unittest.main()
