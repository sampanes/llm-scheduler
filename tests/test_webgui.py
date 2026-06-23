"""Tests for run_gui's HTML-load plumbing in webgui.

The js_api bridge attaches ONLY over a real file:// origin; loading via
html=/NavigateToString gives a null origin and the bridge never attaches, so the
front-end hangs on the boot veil forever. _loadable_app_file is what guarantees
we always hand pywebview a real file (canonical, else a temp fallback) rather
than silently dropping to the bridge-killing html= path. These tests lock that
guarantee in. They don't import pywebview (webgui imports `webview` lazily,
inside run_gui), so they run headless.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import webgui


class LoadableAppFile(unittest.TestCase):
    def test_uses_canonical_when_current(self):
        with tempfile.TemporaryDirectory() as d:
            webui = Path(d)
            (webui / "_app.html").write_text("<html>X</html>", encoding="utf-8")
            with mock.patch.object(webgui, "WEBUI_DIR", webui):
                got = webgui._loadable_app_file("<html>X</html>")
            self.assertEqual(got, webui / "_app.html")
            self.assertEqual(got.read_text(encoding="utf-8"), "<html>X</html>")

    def test_falls_back_to_temp_when_canonical_missing(self):
        with tempfile.TemporaryDirectory() as d:
            webui = Path(d)  # no _app.html written -> canonical absent
            with mock.patch.object(webgui, "WEBUI_DIR", webui):
                got = webgui._loadable_app_file("<html>FRESH</html>")
            self.assertIsNotNone(got)
            self.assertNotEqual(got, webui / "_app.html")
            self.assertEqual(got.read_text(encoding="utf-8"), "<html>FRESH</html>")

    def test_falls_back_to_temp_when_canonical_stale(self):
        # The exact stuck-veil trigger: an _app.html exists but is STALE (a write
        # lock left it behind). We must NOT load the stale file -- write a fresh
        # temp file with the current html instead.
        with tempfile.TemporaryDirectory() as d:
            webui = Path(d)
            (webui / "_app.html").write_text("<html>OLD</html>", encoding="utf-8")
            with mock.patch.object(webgui, "WEBUI_DIR", webui):
                got = webgui._loadable_app_file("<html>NEW</html>")
            self.assertNotEqual(got, webui / "_app.html")
            self.assertEqual(got.read_text(encoding="utf-8"), "<html>NEW</html>")

    def test_returns_none_when_nothing_writable(self):
        # Both canonical and temp unwritable -> None (caller degrades to html=).
        missing = Path(tempfile.gettempdir()) / "claude-at-no-such-dir-xyz"
        with mock.patch.object(webgui, "WEBUI_DIR", missing), \
             mock.patch("tempfile.gettempdir", return_value=str(missing)):
            got = webgui._loadable_app_file("<html>X</html>")
        self.assertIsNone(got)

    def test_temp_path_is_file_uri_loadable(self):
        # Whatever it returns must resolve to an absolute path (file:// needs it).
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(webgui, "WEBUI_DIR", Path(d)):
                got = webgui._loadable_app_file("<html>X</html>")
            self.assertTrue(got.resolve().as_uri().startswith("file://"))


if __name__ == "__main__":
    unittest.main()
