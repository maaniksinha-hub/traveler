#!/usr/bin/env python3
"""Assemble web/traveler.html from its source parts.

Run this after editing anything under web/build/, web/traveler.mjs, or
web/data/airports-data.mjs -- traveler.html is generated, not hand-edited.

    python3 web/build/assemble.py

Parts combined, in order:
  1. web/build/body.html      -- markup + <meta charset> + <title>
  2. web/build/style.css.tmpl -- design tokens/components, {{FONT}} placeholders
  3. web/fonts/*.b64          -- Geist + Geist Mono, base64, substituted into CSS
  4. web/traveler.mjs         -- the ported decision engine (import line
                                  stripped; AIRPORTS_RAW inlined from
                                  web/data/airports-data.mjs instead)
  5. web/build/app-ui.js      -- form handling + results rendering
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # web/
BUILD = ROOT / "build"
FONTS = ROOT / "fonts"


def main() -> None:
    css = (BUILD / "style.css.tmpl").read_text()
    b64 = {
        "GEIST_REGULAR": (FONTS / "geist-regular.b64").read_text().strip(),
        "GEIST_MEDIUM": (FONTS / "geist-medium.b64").read_text().strip(),
        "GEIST_SEMIBOLD": (FONTS / "geist-semibold.b64").read_text().strip(),
        "GEIST_MONO_REGULAR": (FONTS / "geist-mono-regular.b64").read_text().strip(),
        "GEIST_MONO_MEDIUM": (FONTS / "geist-mono-medium.b64").read_text().strip(),
    }
    for key, val in b64.items():
        placeholder = "{{" + key + "}}"
        if placeholder not in css:
            raise SystemExit(f"style.css.tmpl is missing placeholder {placeholder}")
        css = css.replace(placeholder, val)

    body = (BUILD / "body.html").read_text()

    engine_src = (ROOT / "traveler.mjs").read_text()
    import_line = 'import { AIRPORTS_RAW } from "./data/airports-data.mjs";\n'
    if import_line not in engine_src:
        raise SystemExit("traveler.mjs: expected import line not found -- did it move?")
    engine_src = engine_src.replace(import_line, "")

    airports_module = (ROOT / "data" / "airports-data.mjs").read_text()
    start = airports_module.index("`") + 1
    end = airports_module.rindex("`")
    airports_const = "const AIRPORTS_RAW = `" + airports_module[start:end] + "`;\n\n"

    marker = "const REGISTRY = new Map();"
    if marker not in engine_src:
        raise SystemExit("traveler.mjs: REGISTRY marker not found -- did routing.js move?")
    engine_src = engine_src.replace(marker, airports_const + marker, 1)

    app_ui = (BUILD / "app-ui.js").read_text()

    out = (
        body
        + "\n<style>\n" + css + "\n</style>\n"
        + '\n<script type="module">\n'
        + engine_src
        + "\n\n// ============================================================\n"
        + "// UI layer (web/build/app-ui.js)\n"
        + "// ============================================================\n\n"
        + app_ui
        + "\n</script>\n"
    )

    dest = ROOT / "traveler.html"
    dest.write_text(out)
    print(f"wrote {dest} ({len(out):,} bytes)")


if __name__ == "__main__":
    main()
