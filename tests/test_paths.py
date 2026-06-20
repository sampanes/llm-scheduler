"""Unit tests for catcore.paths executable resolution.

Focus on the soundness fix: the settings-supplied path checks use .is_file(),
not .exists(), so a path pointing at a *directory* is rejected (it can't be the
task action) rather than accepted and failed cryptically at fire time.

The "rejects a directory" assertions don't depend on PATH / install-location
fallbacks: whatever those resolve to, the result must simply never be the
directory we passed in. The "accepts a real file" assertions pass a real file,
which always wins at the first check.
"""

import tempfile
import unittest
from pathlib import Path


from unittest import mock

from catcore.paths import resolve_claude, resolve_codex, resolve_terminal, default_terminal


class ResolveClaude(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.exe = self.dir / "claude.exe"
        self.exe.write_text("", encoding="utf-8")

    def test_real_file_path_is_returned(self):
        self.assertEqual(resolve_claude({"claude_path": str(self.exe)}), str(self.exe))

    def test_directory_path_is_rejected(self):
        # A directory is not a file -> must not be accepted as the executable.
        self.assertNotEqual(resolve_claude({"claude_path": str(self.dir)}), str(self.dir))

    def test_empty_path_does_not_crash(self):
        # No settings path -> falls through to which()/known locations (any result
        # is fine; it just must not raise).
        resolve_claude({})


class ResolveCodex(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.exe = self.dir / "codex.exe"
        self.exe.write_text("", encoding="utf-8")

    def test_real_file_path_is_returned(self):
        self.assertEqual(resolve_codex({"codex_path": str(self.exe)}), str(self.exe))

    def test_directory_path_is_rejected(self):
        self.assertNotEqual(resolve_codex({"codex_path": str(self.dir)}), str(self.dir))


class ResolveTerminal(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.exe = self.dir / "term.exe"
        self.exe.write_text("", encoding="utf-8")

    def test_wezterm_real_file_is_returned(self):
        self.assertEqual(
            resolve_terminal({"wezterm_path": str(self.exe)}, "wezterm"), str(self.exe))

    def test_wezterm_directory_is_rejected(self):
        self.assertNotEqual(
            resolve_terminal({"wezterm_path": str(self.dir)}, "wezterm"), str(self.dir))

    def test_wt_real_file_is_returned(self):
        self.assertEqual(
            resolve_terminal({"wt_path": str(self.exe)}, "wt"), str(self.exe))

    def test_wt_directory_is_rejected(self):
        self.assertNotEqual(
            resolve_terminal({"wt_path": str(self.dir)}, "wt"), str(self.dir))

    def test_console_is_none(self):
        self.assertIsNone(resolve_terminal({}, "console"))


class DefaultTerminal(unittest.TestCase):
    """The auto-pick when settings.json pins no terminal: first installed of
    wezterm / wt, else console. Patched so it doesn't depend on what's actually
    on the test box -- keeps a fresh clone working without assuming wezterm."""

    def test_prefers_wezterm_when_present(self):
        with mock.patch("catcore.paths.resolve_terminal",
                        side_effect=lambda s, t: "X" if t == "wezterm" else None):
            self.assertEqual(default_terminal({}), "wezterm")

    def test_falls_back_to_wt(self):
        with mock.patch("catcore.paths.resolve_terminal",
                        side_effect=lambda s, t: "X" if t == "wt" else None):
            self.assertEqual(default_terminal({}), "wt")

    def test_console_when_neither_installed(self):
        with mock.patch("catcore.paths.resolve_terminal", return_value=None):
            self.assertEqual(default_terminal({}), "console")


if __name__ == "__main__":
    unittest.main()
