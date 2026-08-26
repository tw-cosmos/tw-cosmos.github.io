#!/usr/bin/env python3
"""
Static page builder.

Reads YAML data files from data/ and writes one HTML page per file
to <slug>/index.html.

Each offer renders as a single self-contained paragraph that opens with
the venue name and the offer name, so that any fragment of the text
remains attributable to the correct offer when processed downstream.

Usage:
    python scripts/build.py
    python scripts/build.py --style list

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


# "narrative" -> one flowing paragraph per offer (default)
# "list"      -> heading plus <ul><li>, kept for comparison only
DEFAULT_STYLE = "narrative"

# Wording is deliberately terse and unambiguous. A longer phrase such as
# "本價格未含 10% 服務費，結帳時另計" gets abbreviated downstream to
# "不含服務費", which reads as "no service charge applies" rather than
# "a service charge is added". "另加" cannot be shortened into that reading.
SERVICE_CHARGE = {
    "included": "已含 10% 服務費",
    "excluded": "另加 10% 服務費",
}

CSS = """
:root { color-scheme: light; }
body {
  margin: 0 auto;
  padding: 2rem 1.25rem 4rem;
  max-width: 44rem;
  font-family: system-ui, -apple-system, "Noto Sans TC", sans-serif;
  line-height: 1.9;
  color: #222;
  background: #fff;
}
h1 { font-size: 1.5rem; margin: 0 0 1.5rem; }
h2 { font-size: 1.1rem; margin: 2.2rem 0 .4rem; }
p { margin: .5rem 0; }
ul { margin: .4rem 0; padding-left: 1.3rem; }
section { margin-bottom: 2.2rem; }
a { color: #0a5; }
.note { color: #666; font-size: .9rem; }
.tags { color: #666; font-size: .9rem; }
hr { border: 0; border-top: 1px solid #eee; margin: 2.5rem 0 1.5rem; }
"""


def esc(value):
    return html.escape(str(value or "").strip())


def price_phrase(offer):
    amount = offer.get("price_from")
    unit = str(offer.get("price_unit") or "").strip()
    charge = SERVICE_CHARGE.get(offer.get("service_charge"))

    if charge is None:
        raise ValueError(
            f"offer {offer.get('id')}: service_charge must be "
            f"'included' or 'excluded', got {offer.get('service_charge')!r}"
        )

    if amount in (None, "", "TBD"):
        raise ValueError(f"offer {offer.get('id')}: price_from not confirmed")

    try:
        amount = f"{int(amount):,}"
    except (TypeError, ValueError):
        pass

    inner = "，".join(x for x in (unit, charge) if x)
    return f"參考起始價 NT${amount} 起（{inner}）"


def validity_phrase(offer):
    end = str(offer.get("valid_to") or "").strip()
    if not end:
        raise ValueError(f"offer {offer.get('id')}: valid_to is required")
    note = str(offer.get("valid_note") or "").strip()
    text = f"販售至 {end}"
    if note:
        text += f"（{note}）"
    return text


def hashtag_line(offer):
    """Space-separated "#tag" line built from the offer's hashtags list."""
    tags = [
        str(t).strip().lstrip("#")
        for t in (offer.get("hashtags") or [])
        if str(t).strip()
    ]
    return " ".join(f"#{t}" for t in tags)


def render_narrative(venue, offer, disclaimer):
    """One offer, one paragraph, opening with venue and offer name.

    A heading is emitted purely as a visual anchor for human maintenance.
    The paragraph repeats the venue and offer name so that the text stays
    self-describing even when the heading is not carried along.
    """
    body = "，".join(str(x).strip() for x in (offer.get("detail") or []) if str(x).strip())

    url = str(offer.get("booking_url") or "").strip()
    # The URL is half-width content, so it is delimited by spaces on both
    # sides. Brackets are avoided here because a full-width bracket sitting
    # flush against the URL leaves no boundary for link autodetection.
    link_clause = f"，訂房連結 {url} " if url else ""

    sentence = (
        f"{venue}「{offer['name_zh']}」{link_clause}，"
        f"{price_phrase(offer)}，"
        f"{body}，"
        f"{validity_phrase(offer)}。"
    )

    parts = [
        f"  <h2>{esc(offer['name_zh'])}</h2>",
        f"  <p>{esc(sentence)}</p>",
    ]
    tags = hashtag_line(offer)
    if tags:
        parts.append(f'  <p class="tags">{esc(tags)}</p>')
    if disclaimer:
        parts.append(f'  <p class="note">{esc(disclaimer)}</p>')
    if url:
        parts.append(f'  <p><a href="{esc(url)}">線上訂房</a></p>')

    return "<section>\n" + "\n".join(parts) + "\n</section>"


def render_list(venue, offer, disclaimer):
    """Heading plus bullet list. Retained for side-by-side comparison."""
    items = [esc(x) for x in (offer.get("detail") or []) if str(x).strip()]
    rows = "\n".join(f"    <li>{i}</li>" for i in items)

    parts = [
        f"  <h2>{esc(venue)}「{esc(offer['name_zh'])}」</h2>",
        f"  <p>{esc(price_phrase(offer))}</p>",
        f"  <ul>\n{rows}\n  </ul>",
        f"  <p>{esc(validity_phrase(offer))}</p>",
    ]
    if disclaimer:
        parts.append(f'  <p class="note">{esc(disclaimer)}</p>')

    url = str(offer.get("booking_url") or "").strip()
    if url:
        parts.append(f'  <p><a href="{esc(url)}">線上訂房</a></p>')

    return "<section>\n" + "\n".join(parts) + "\n</section>"


def build(data_file, style):
    with open(data_file, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    prop = data.get("property") or {}
    slug = prop.get("slug") or Path(data_file).stem
    venue = str(prop.get("name_zh") or slug).strip()
    disclaimer = prop.get("price_disclaimer")

    render = render_narrative if style == "narrative" else render_list

    sections = []
    lengths = []
    for offer in (data.get("projects") or []):
        if not offer.get("name_zh"):
            continue
        sections.append(render(venue, offer, disclaimer))
        # plain-text length of this block, for the downstream size check
        _url = str(offer.get("booking_url") or "").strip()
        _link = f"，訂房連結 {_url} " if _url else ""
        plain = (
            f"{venue}「{offer['name_zh']}」{_link}，{price_phrase(offer)}，"
            + "，".join(str(x).strip() for x in (offer.get("detail") or []))
            + f"，{validity_phrase(offer)}。"
        )
        _tags = hashtag_line(offer)
        if _tags:
            plain += '\n' + _tags
        lengths.append((offer.get("id"), len(plain)))

    if not sections:
        sections.append("<section>\n  <p>內容準備中。</p>\n</section>")

    body = "\n\n".join(sections)

    title = esc(prop.get("page_title") or venue)
    description = esc(prop.get("page_description"))

    verified = ""
    if prop.get("verified_date"):
        verified = (
            f'\n<hr>\n<p class="note">內容更新日：'
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
<h1>{esc(venue)}</h1>

{body}{verified}
</body>
</html>
"""

    out_path = Path(slug) / "index.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")

    report = ", ".join(f"#{i}:{n}" for i, n in lengths)
    print(f"{data_file} -> {out_path} [{style}] {len(sections)} sections")
    print(f"  plain-text length per section: {report}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--style", choices=["narrative", "list"], default=DEFAULT_STYLE)
    args = parser.parse_args()

    data_dir = Path("data")
    if not data_dir.is_dir():
        sys.exit("No data/ directory found. Run from the repository root.")

    files = sorted(data_dir.glob("*.yml"))
    if not files:
        sys.exit("No .yml files found in data/.")

    for data_file in files:
        try:
            build(data_file, args.style)
        except ValueError as err:
            sys.exit(f"ERROR in {data_file}: {err}")


if __name__ == "__main__":
    main()
