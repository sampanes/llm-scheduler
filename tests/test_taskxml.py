"""Unit tests for catcore.taskxml.

build_claude_args and build_trigger_xml are fully pure. build_action /
build_task_xml call resolve_claude / resolve_terminal, which only honor a
settings path that exists ON DISK — so we patch those two boundaries with fake
executable paths to keep the tests machine-independent. The headline test is
the SECURITY INVARIANT: the fire-time action must be a signed binary only
(wezterm / wt / claude), never a script host (powershell / cmd / python).
"""

import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from catcore.config import DAY_NAMES
from catcore.taskxml import (
    build_claude_args, build_trigger_xml, build_action, build_task_xml,
)

FAKE_CLAUDE = r"C:\fake\bin\claude.exe"
FAKE_WEZTERM = r"C:\fake\WezTerm\wezterm-gui.exe"
FAKE_WT = r"C:\fake\wt.exe"
SCRIPT_HOSTS = ("powershell", "cmd.exe", "pythonw", "python.exe")


def job(**over):
    base = {
        "model": "opus", "permission_mode": "auto", "terminal": "wezterm",
        "dir": r"C:\Projects\work space", "name": "nightly", "id": "abc12345",
        "target": {"mode": "continue", "session_id": ""},
        "schedule": {"type": "once", "datetime": "2099-01-01T06:45"},
        "prompt": "", "extra_args": "",
        "require_network": True, "delete_after_run": True,
    }
    base.update(over)
    return base


def xml_body(s):
    """Strip the UTF-16 XML declaration so ElementTree parses the str body
    (ET would otherwise choke on the encoding decl); parsing at all proves the
    document is well-formed, i.e. dynamic content was escaped correctly."""
    return ET.fromstring(s[s.index("?>") + 2:])


class BuildClaudeArgs(unittest.TestCase):
    def test_continue_includes_model_mode_and_continue(self):
        self.assertEqual(
            build_claude_args(job(target={"mode": "continue", "session_id": ""}), {}),
            ["--model", "opus", "--permission-mode", "auto", "--continue"])

    def test_resume_passes_session_id(self):
        args = build_claude_args(job(target={"mode": "resume", "session_id": "sid-9"}), {})
        self.assertIn("--resume", args)
        self.assertEqual(args[args.index("--resume") + 1], "sid-9")

    def test_new_adds_neither_continue_nor_resume(self):
        args = build_claude_args(job(target={"mode": "new", "session_id": ""}), {})
        self.assertNotIn("--continue", args)
        self.assertNotIn("--resume", args)

    def test_extra_args_quoted_windows_path_survives(self):
        args = build_claude_args(
            job(extra_args=r'--fork-session --add-dir "C:\proj dir"', prompt="go on"), {})
        self.assertEqual(args, [
            "--model", "opus", "--permission-mode", "auto", "--continue",
            "--fork-session", "--add-dir", r"C:\proj dir", "go on"])


class BuildTriggerXml(unittest.TestCase):
    def test_once_has_end_boundary_when_deleting(self):
        x = build_trigger_xml(job(schedule={"type": "once", "datetime": "2099-01-01T06:45"},
                                  delete_after_run=True), {"missed_run_window_hours": 25})
        self.assertIn("<TimeTrigger>", x)
        self.assertIn("<StartBoundary>2099-01-01T06:45:00</StartBoundary>", x)
        self.assertIn("<EndBoundary>", x)

    def test_once_no_end_boundary_when_keeping(self):
        x = build_trigger_xml(job(schedule={"type": "once", "datetime": "2099-01-01T06:45"},
                                  delete_after_run=False), {})
        self.assertNotIn("<EndBoundary>", x)

    def test_daily_keeps_time(self):
        x = build_trigger_xml(job(schedule={"type": "daily", "time": "06:45"}), {})
        self.assertIn("<CalendarTrigger>", x)
        self.assertIn("T06:45:00</StartBoundary>", x)
        self.assertIn("<DaysInterval>1</DaysInterval>", x)

    def test_weekly_maps_day_names(self):
        x = build_trigger_xml(job(schedule={"type": "weekly", "days": ["MON", "FRI"], "time": "06:45"}), {})
        self.assertIn(f"<{DAY_NAMES['MON']} />", x)
        self.assertIn(f"<{DAY_NAMES['FRI']} />", x)

    def test_unknown_type_raises(self):
        with self.assertRaises(ValueError):
            build_trigger_xml(job(schedule={"type": "fortnightly", "time": "06:45"}), {})


@patch("catcore.taskxml.resolve_terminal")
@patch("catcore.taskxml.resolve_claude", return_value=FAKE_CLAUDE)
class BuildAction(unittest.TestCase):
    def test_wezterm_wraps_claude(self, _claude, term):
        term.return_value = FAKE_WEZTERM
        cmd, argstr, wd = build_action(job(terminal="wezterm"), {})
        self.assertEqual(cmd, FAKE_WEZTERM)
        self.assertTrue(argstr.startswith("start --cwd"))
        self.assertIn("--", argstr)
        self.assertIn("claude.exe", argstr)
        self.assertEqual(wd, r"C:\Projects\work space")

    def test_wt_wraps_claude(self, _claude, term):
        term.return_value = FAKE_WT
        cmd, argstr, _ = build_action(job(terminal="wt"), {})
        self.assertEqual(cmd, FAKE_WT)
        self.assertTrue(argstr.startswith("-d"))
        self.assertIn("claude.exe", argstr)

    def test_console_runs_claude_directly(self, _claude, term):
        cmd, argstr, _ = build_action(job(terminal="console"), {})
        self.assertEqual(cmd, FAKE_CLAUDE)
        self.assertNotIn("--cwd", argstr)

    def test_missing_claude_raises(self, claude, _term):
        claude.return_value = None
        with self.assertRaises(RuntimeError):
            build_action(job(terminal="console"), {})

    def test_security_no_script_host_in_action(self, _claude, term):
        """The fire-time command is a signed binary, never a script host."""
        term.return_value = FAKE_WEZTERM
        for terminal in ("wezterm", "wt", "console"):
            term.return_value = FAKE_WT if terminal == "wt" else FAKE_WEZTERM
            cmd, argstr, _ = build_action(job(terminal=terminal), {})
            for bad in SCRIPT_HOSTS:
                self.assertNotIn(bad, cmd.lower(), f"{terminal}: {bad} in command")
                self.assertNotIn(bad, argstr.lower(), f"{terminal}: {bad} in args")


@patch("catcore.taskxml.resolve_terminal", return_value=FAKE_WEZTERM)
@patch("catcore.taskxml.resolve_claude", return_value=FAKE_CLAUDE)
class BuildTaskXml(unittest.TestCase):
    def test_well_formed_and_escapes_prompt(self, _claude, _term):
        # ampersand + angle bracket in the prompt must be escaped or parsing fails
        x = build_task_xml(job(prompt='fix A && B < C', terminal="console"), {})
        xml_body(x)  # raises on malformed XML

    def test_interactive_invariants_present(self, _claude, _term):
        x = build_task_xml(job(terminal="console"), {})
        self.assertIn("<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>", x)
        self.assertIn("<MultipleInstancesPolicy>Parallel</MultipleInstancesPolicy>", x)
        self.assertIn("<WakeToRun>true</WakeToRun>", x)

    def test_once_delete_adds_delete_expired(self, _claude, _term):
        x = build_task_xml(job(schedule={"type": "once", "datetime": "2099-01-01T06:45"},
                               delete_after_run=True, terminal="console"),
                           {"missed_run_window_hours": 25})
        self.assertIn("<DeleteExpiredTaskAfter>PT1H</DeleteExpiredTaskAfter>", x)

    def test_require_network_reflected(self, _claude, _term):
        on = build_task_xml(job(require_network=True, terminal="console"), {})
        off = build_task_xml(job(require_network=False, terminal="console"), {})
        self.assertIn("<RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>", on)
        self.assertIn("<RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>", off)


if __name__ == "__main__":
    unittest.main()
