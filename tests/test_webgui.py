"""Tests for run_gui's HTML-load plumbing in webgui.

The js_api bridge attaches ONLY over a real file:// origin; loading via
html=/NavigateToString gives a null origin and the bridge never attaches, so the
front-end hangs on the boot veil forever. _loadable_app_file is what guarantees
we always hand pywebview a real file (canonical, else a temp fallback) rather
than silently dropping to the bridge-killing html= path. These tests lock that
guarantee in. They don't import pywebview (webgui imports `webview` lazily,
inside run_gui), so they run headless.
"""

import inspect
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


class ApiBridgeWalkSafety(unittest.TestCase):
    """pywebview's inject_pywebview walks dir(js_api) to discover exposed
    methods (util.py get_functions) and RECURSES into every non-underscore,
    non-callable attribute that has a __module__. A public attribute holding the
    native window (a WinForms Form) sends it down .native.AccessibilityObject.
    Bounds.Empty.Empty… — an infinite pythonnet Rectangle chain — until
    RecursionError escapes mid-inject, so `loaded` never fires and the bridge
    never attaches (the boot veil hangs forever, reliably under pythonw). This
    locks in that NO public Api attribute can trigger that recursion."""

    def _recursable(self, attr):
        # Mirrors pywebview util.get_functions' recurse predicate exactly:
        # non-callable objects that are a class or expose __module__.
        if callable(attr):
            return False
        return inspect.isclass(attr) or hasattr(attr, "__module__")

    def test_no_public_attr_makes_pywebview_recurse(self):
        api = webgui.Api({"model": "opus", "permission_mode": "auto",
                          "terminal": "", "effort": ""})
        # Stand in for the real native window: any object with __module__ is what
        # pywebview would recurse into. (The real one's graph is infinite.)
        api._window = object()
        offenders = [
            name for name in dir(api)
            if not name.startswith("_") and self._recursable(getattr(api, name))
        ]
        self.assertEqual(
            offenders, [],
            f"public Api attribute(s) {offenders} would make pywebview recurse "
            f"into their native object graph and hang the bridge; prefix with '_'")

    def test_window_handle_is_underscored(self):
        api = webgui.Api({})
        self.assertTrue(hasattr(api, "_window"))
        self.assertFalse(hasattr(api, "window"))


if __name__ == "__main__":
    unittest.main()
