# Brand pack

The app's look is defined here — edit `brand.json` and drop in your own logo to
re-theme; no code changes. See the **Theming** section of the top-level
`README.md` for the full field reference.

## Files

- **`brand.json`** — the brand definition: `name`, `accent` (+ optional
  `accentHover` / `accentActive`), `logo`, `appId`, and `font` (`stack` +
  bundled `files`). This is the only file you normally edit.
- **`fonts/lato/`** — the bundled default font (Lato, SIL Open Font License).
  Swap in your own TTFs and point `brand.json` `font.files` at them, or set
  `"files": []` to fall back to a system font from `font.stack`.
- **`icons/`** — UI glyphs. `claude-at.ico` (window/taskbar icon) and
  `icon-spark.svg` (the in-app coral accent) mark this as a Claude tool and are
  kept across brands; the rest are generic controls.

## Add your logo

1. Drop an `.svg` (preferred) or a raster (`.png`, …) into a `logos/` folder here.
2. Set `"logo": "logos/your-logo.svg"` in `brand.json`.

It's embedded at launch (SVG inlined, raster as a data-URI) and shown top-left
and on the boot splash. Leave `"logo": null` to show just the wordmark.
