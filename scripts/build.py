#!/usr/bin/env python3
"""
Static page builder.

Reads YAML data files from data/ and writes one HTML page per file
to <slug>/index.html.

Usage:
    python scripts/build.py
    python scripts/build.py --style ul

Requires: pip install pyyaml
"""

import argparse
import html
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("Missing dependency. Run: pip install pyyaml")


# Rendering mode for project detail lines.
#   "inline" -> single line, items joined by a full-width enumeration comma
#   "ul"     -> <ul><li> list
# Kept as a switch so the same data file can produce either layout.
DEFAULT_STYLE = "inline"

CSS = """
:root { color-scheme: light; }
body {
  margin: 0 auto;
  padding: 2rem 1.25rem 4rem;
  max-width: 44rem;
  font-family: system-ui, -apple-system, "Noto Sans TC", sans-serif;
  line-height: 1.85;
  color: #222;
  background: #fff;
}
h1 { font-size: 1.5rem; margin: 0 0 .5rem; }
h2 { font-size: 1.15rem; margin: 2.5rem 0 .5rem; }
p { margin: .4rem 0; }
ul { margin: .4rem 0; padding-left: 1.3rem; }
li { margin: .2rem 0; }
a { color: #0a5; }
.note { color: #666; font-size: .9rem; }
hr { border: 0; border-top: 1px solid #eee; margin: 2rem 0; }
"""


def esc(value):
    return html.escape(str(value or "").strip())


def render_detail(items, style):
    """Render the detail lines of one project."""
    clean = [esc(i) for i in (items or []) if str(i).strip()]
    if not clean:
        return ""
    if style == "ul":
        rows = "\n".join(f"    <li>{i}</li>" for i in clean)
        return f"  <ul>\n{rows}\n  </ul>"
    return "  <p>" + "、".join(clean) + "</p>"


def render_price(project, disclaimer):
    """Reference starting price plus the shared floating-price note."""
    amount = project.get("price_from")
    unit = esc(project.get("price_unit"))

    if amount in (None, "", "TBD"):
        head = "參考起始價 : 待更新"
    else:
        try:
            amount = f"{int(amount):,}"
        except (TypeError, ValueError):
            amount = esc(amount)
        head = f"參考起始價 : NT${amount} 起"

    if unit:
        head = f"{head} ( {unit} )"

    out = [f"  <p>{head}</p>"]
    if disclaimer:
        out.append(f'  <p class="note">{esc(disclaimer)}</p>')
    return "\n".join(out)


def render_validity(project):
    start = esc(project.get("valid_from"))
    end = esc(project.get("valid_to"))
    note = esc(project.get("valid_note"))

    if not start and not end:
        return ""
    line = f"適用期間 : {start} - {end}"
    if note:
        line = f"{line} ( {note} )"
    return f"  <p>{line}</p>"


def render_link(project):
    url = esc(project.get("booking_url"))
    if not url:
        return ""
    return f'  <p><a href="{url}">線上訂房</a></p>'


def render_project(project, disclaimer, style):
    """One project = one <section>, no blank lines inside."""
    name_zh = esc(project.get("name_zh"))
    name_en = esc(project.get("name_en"))
    if not name_zh and not name_en:
        return ""

    heading = " ".join(x for x in (name_zh, name_en) if x)

    parts = [f"  <h2>{heading}</h2>"]
    for chunk in (
        render_price(project, disclaimer),
        render_detail(project.get("detail"), style),
        render_validity(project),
        render_link(project),
    ):
        if chunk:
            parts.append(chunk)

    return "<section>\n" + "\n".join(parts) + "\n</section>"


def build(data_file, style):
    with open(data_file, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    prop = data.get("property") or {}
    slug = prop.get("slug") or Path(data_file).stem
    disclaimer = prop.get("price_disclaimer")

    sections = []
    for project in (data.get("projects") or []):
        block = render_project(project, disclaimer, style)
        if block:
            sections.append(block)

    if not sections:
        sections.append("<section>\n  <p>內容準備中。</p>\n</section>")

    # Sections are separated by a blank line so that the rendered plain
    # text has a paragraph break between projects, and none within one.
    body = "\n\n".join(sections)

    title = esc(prop.get("page_title") or prop.get("name_zh") or slug)
    description = esc(prop.get("page_description"))
    heading = esc(prop.get("name_zh") or slug)

    verified = ""
    if prop.get("verified_date"):
        verified = (
            f'\n<hr>\n<p class="note">內容更新日 : '
            f'{esc(prop.get("verified_date"))}</p>'
        )

    page = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex, nofollow">
<title>{title}</title>
<meta name="description" content="{description}">
<style>{CSS}</style>
</head>
<body>
<h1>{heading}</h1>

{body}{verified}
</body>
</html>
"""

    out_path = Path(slug) / "index.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")
    print(f"{data_file} -> {out_path} ({len(sections)} sections, style={style})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--style",
        choices=["inline", "ul"],
        default=DEFAULT_STYLE,
        help="How to render project detail lines.",
    )
    args = parser.parse_args()

    data_dir = Path("data")
    if not data_dir.is_dir():
        sys.exit("No data/ directory found. Run from the repository root.")

    files = sorted(data_dir.glob("*.yml"))
    if not files:
        sys.exit("No .yml files found in data/.")

    for data_file in files:
        build(data_file, args.style)


if __name__ == "__main__":
    main()
