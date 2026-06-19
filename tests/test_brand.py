"""Tests for the swappable brand-pack mechanism in webgui.

These are deliberately BRAND-AGNOSTIC: they exercise the machinery (hex->rgb,
the :root override, logo SVG/raster handling, brand.json load + merge, and that
compose_html injects whatever the active pack declares) without asserting any
one brand's colours. So they pass identically on the white-label `main` and on a
branded branch -- the whole point of the brand pack.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import webgui


class HexToRgb(unittest.TestCase):
    def test_six_digit(self):
        self.assertEqual(webgui._hex_to_rgb("#e31f26"), (227, 31, 38))

    def test_no_hash(self):
        self.assertEqual(webgui._hex_to_rgb("ffffff"), (255, 255, 255))

    def test_three_digit_shorthand(self):
        self.assertEqual(webgui._hex_to_rgb("#abc"), (170, 187, 204))

    def test_unparseable_falls_back_to_coral(self):
        self.assertEqual(webgui._hex_to_rgb("nope"), (193, 95, 60))
        self.assertEqual(webgui._hex_to_rgb(None), (193, 95, 60))


class BrandRootCss(unittest.TestCase):
    def test_emits_accent_rgb_and_font(self):
        css = webgui._brand_root_css({
            "accent": "#123456", "accentHover": "#111111",
            "accentActive": "#222222", "font": {"stack": "'Foo', sans-serif"},
        })
        self.assertIn("--brand-accent:#123456", css)
        self.assertIn("--brand-accent-hover:#111111", css)
        self.assertIn("--brand-accent-active:#222222", css)
        self.assertIn("--brand-accent-rgb:18, 52, 86", css)
        self.assertIn("--font-primary:'Foo', sans-serif", css)

    def test_hover_active_default_to_accent(self):
        css = webgui._brand_root_css({"accent": "#abcdef", "font": {}})
        self.assertIn("--brand-accent-hover:#abcdef", css)
        self.assertIn("--brand-accent-active:#abcdef", css)

    def test_no_color_block_when_colors_absent(self):
        css = webgui._brand_root_css({"accent": "#abcdef", "font": {}})
        self.assertNotIn("[data-theme=", css)

    def test_color_overrides_are_theme_scoped(self):
        css = webgui._brand_root_css({
            "accent": "#abcdef", "font": {},
            "colors": {"light": {"bg": "#fafafa", "surface": "#ffffff"},
                       "dark": {"bg": "#101010"}},
        })
        self.assertIn('[data-theme="light"]{', css)
        self.assertIn("--bg:#fafafa;", css)
        self.assertIn("--surface:#ffffff;", css)
        self.assertIn('[data-theme="dark"]{--bg:#101010;}', css)

    def test_unknown_color_keys_ignored(self):
        css = webgui._brand_root_css({
            "accent": "#abcdef", "font": {},
            "colors": {"light": {"bogus": "#fff"}},
        })
        self.assertNotIn("[data-theme=", css)  # nothing valid -> no block


class FontFaceCss(unittest.TestCase):
    def test_empty_when_no_files(self):
        self.assertEqual(webgui._font_face_css({"font": {"files": []}}), "")

    def test_skips_missing_files(self):
        brand = {"font": {"family": "X",
                          "files": [{"file": "nope/missing.ttf", "weight": 400}]}}
        self.assertEqual(webgui._font_face_css(brand), "")


class LogoMarkup(unittest.TestCase):
    def test_none_when_unset(self):
        self.assertIsNone(webgui._logo_markup({"logo": None}))

    def test_svg_inlined(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "logo.svg"
            p.write_text("<svg><circle/></svg>", encoding="utf-8")
            with mock.patch.object(webgui, "BRAND", Path(d)):
                out = webgui._logo_markup({"logo": "logo.svg"})
        self.assertEqual(out, "<svg><circle/></svg>")

    def test_raster_becomes_data_uri_img(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "logo.png"
            p.write_bytes(b"\x89PNG\r\n\x1a\n fake")
            with mock.patch.object(webgui, "BRAND", Path(d)):
                out = webgui._logo_markup({"logo": "logo.png"})
        self.assertIn('<img src="data:image/png;base64,', out)

    def test_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(webgui, "BRAND", Path(d)):
                self.assertIsNone(webgui._logo_markup({"logo": "absent.svg"}))


class LoadBrand(unittest.TestCase):
    def test_defaults_when_file_missing(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(webgui, "BRAND_FILE", Path(d) / "nope.json"):
                b = webgui.load_brand()
        self.assertEqual(b["name"], "claude-at")
        self.assertEqual(b["accent"], "#c15f3c")
        self.assertEqual(b["font"]["files"], [])
        self.assertIsNone(b["logo"])

    def test_overrides_merge_over_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "brand.json"
            f.write_text(json.dumps({
                "name": "Acme", "accent": "#00ff00",
                "font": {"stack": "'Acme Sans', sans-serif"},
            }), encoding="utf-8")
            with mock.patch.object(webgui, "BRAND_FILE", f):
                b = webgui.load_brand()
        self.assertEqual(b["name"], "Acme")
        self.assertEqual(b["accent"], "#00ff00")
        # nested font dict merges, not replaces: stack overridden, family kept.
        self.assertEqual(b["font"]["stack"], "'Acme Sans', sans-serif")
        self.assertEqual(b["font"]["family"], "Lato")

    def test_garbage_json_degrades_to_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "brand.json"
            f.write_text("[not, a, dict]", encoding="utf-8")
            with mock.patch.object(webgui, "BRAND_FILE", f):
                b = webgui.load_brand()
        self.assertEqual(b["name"], "claude-at")


class ComposeInjectsActiveBrand(unittest.TestCase):
    def test_active_accent_reaches_the_page(self):
        """Whatever brand pack is active, its accent lands in a --brand-accent
        override and every injection token is filled."""
        brand = webgui.load_brand()
        html = webgui.compose_html(write_preview=False)
        self.assertIn(f"--brand-accent:{brand['accent']}", html)
        for token in ("<!--FONTS-->", "<!--APP_CSS-->", "<!--BRAND_CSS-->",
                      "<!--ICONS_JS-->", "<!--APP_JS-->", "<!--BRAND_NAME-->"):
            self.assertNotIn(token, html)


class Presets(unittest.TestCase):
    """Canned themes: brand.json {"preset": "<name>"} pulls a look from
    presets.json, with brand.json's own keys still winning over it."""

    def _files(self, d, brand_obj, presets_obj):
        bf = Path(d) / "brand.json"
        pf = Path(d) / "presets.json"
        bf.write_text(json.dumps(brand_obj), encoding="utf-8")
        pf.write_text(json.dumps(presets_obj), encoding="utf-8")
        return bf, pf

    def _load(self, d, brand_obj, presets_obj):
        bf, pf = self._files(d, brand_obj, presets_obj)
        with mock.patch.object(webgui, "BRAND_FILE", bf), \
                mock.patch.object(webgui, "PRESETS_FILE", pf):
            return webgui.load_brand()

    def test_named_preset_applies(self):
        with tempfile.TemporaryDirectory() as d:
            b = self._load(d, {"preset": "ocean"}, {"ocean": {"accent": "#2563eb"}})
        self.assertEqual(b["accent"], "#2563eb")
        self.assertNotIn("preset", b)  # the selector never leaks into the brand

    def test_explicit_keys_override_preset(self):
        with tempfile.TemporaryDirectory() as d:
            b = self._load(d, {"preset": "ocean", "accent": "#ff0000"},
                           {"ocean": {"accent": "#2563eb"}})
        self.assertEqual(b["accent"], "#ff0000")

    def test_unknown_preset_falls_through(self):
        with tempfile.TemporaryDirectory() as d:
            b = self._load(d, {"preset": "nope", "accent": "#abcdef"},
                           {"ocean": {"accent": "#2563eb"}})
        self.assertEqual(b["accent"], "#abcdef")

    def test_no_preset_is_defaults_plus_brand(self):
        """The crux: without a preset, resolution is exactly defaults+brand.json
        -- so adding the preset layer changes nothing for an unbranded clone."""
        with tempfile.TemporaryDirectory() as d:
            b = self._load(d, {"accent": "#abcdef"}, {"ocean": {"accent": "#2563eb"}})
        self.assertEqual(b["accent"], "#abcdef")
        self.assertEqual(b["name"], "claude-at")        # default kept
        self.assertEqual(b["font"]["family"], "Lato")   # default font kept

    def test_preset_font_merges_not_replaces(self):
        with tempfile.TemporaryDirectory() as d:
            b = self._load(d, {"preset": "withfont"},
                           {"withfont": {"font": {"stack": "'X', sans-serif"}}})
        self.assertEqual(b["font"]["stack"], "'X', sans-serif")
        self.assertEqual(b["font"]["family"], "Lato")   # nested merge, not replace

    def test_shipped_presets_are_valid(self):
        """The presets.json that ships in the repo is a non-empty flat map of
        name -> dict with a hex accent (guards against a malformed edit)."""
        presets = json.loads(webgui.PRESETS_FILE.read_text(encoding="utf-8"))
        self.assertIsInstance(presets, dict)
        self.assertTrue(presets)
        for name, p in presets.items():
            self.assertIsInstance(p, dict, name)
            self.assertRegex(p.get("accent", ""), r"^#[0-9a-fA-F]{3,6}$", name)


if __name__ == "__main__":
    unittest.main()
