"""Unit tests for catcore.sessions title recovery.

Focus: _find_custom_title reads only the file *tail* (a boot-time I/O win), and
must still recover the newest rename and degrade cleanly. Uses compact JSON with
separators=(',',':') because that's the on-disk shape Claude writes and the byte
pattern the scanner searches for ('"type":"custom-title"', no spaces).
"""

import json
import tempfile
import unittest
from pathlib import Path

from catcore.sessions import _find_custom_title


def cj(obj):
    return json.dumps(obj, separators=(",", ":"))


def write_jsonl(lines):
    d = Path(tempfile.mkdtemp())
    p = d / "session.jsonl"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


class FindCustomTitle(unittest.TestCase):
    def test_none_when_no_rename(self):
        p = write_jsonl([cj({"type": "user", "message": {"content": "hi"}})] * 5)
        self.assertIsNone(_find_custom_title(p))

    def test_empty_file(self):
        self.assertIsNone(_find_custom_title(write_jsonl([])))

    def test_newest_rename_wins(self):
        p = write_jsonl([
            cj({"type": "custom-title", "customTitle": "OLD"}),
            cj({"type": "user", "message": {"content": "work"}}),
            cj({"type": "custom-title", "customTitle": "NEWEST"}),
        ])
        self.assertEqual(_find_custom_title(p), "NEWEST")

    def test_tail_only_read_still_finds_recent_rename(self):
        # A large file whose newest rename sits well past the first 1 KB: the
        # default 256 KB tail must still catch it (this is the whole point of
        # not reading the file head-to-tail on every scan).
        lines = [cj({"type": "custom-title", "customTitle": "OLD"})]
        lines += [cj({"type": "user", "message": {"content": "pad %d %s" % (i, "z" * 200)}})
                  for i in range(4000)]
        lines.append(cj({"type": "custom-title", "customTitle": "NEWEST"}))
        lines += [cj({"type": "assistant", "message": {"content": "reply %d" % i}})
                  for i in range(40)]
        p = write_jsonl(lines)
        self.assertGreater(p.stat().st_size, 262144)  # bigger than the tail window
        self.assertEqual(_find_custom_title(p), "NEWEST")

    def test_tail_window_drops_partial_first_line(self):
        # When the tail starts mid-line, the partial head line must be discarded
        # rather than fed to json.loads as garbage. A rename beyond a tiny tail
        # window is (acceptably) not found; the call must still not raise.
        lines = [cj({"type": "custom-title", "customTitle": "BURIED"})]
        lines += [cj({"type": "user", "message": {"content": "x" * 100}}) for _ in range(50)]
        p = write_jsonl(lines)
        self.assertIsNone(_find_custom_title(p, tail_bytes=64))


if __name__ == "__main__":
    unittest.main()
