#!/usr/bin/env python3
"""
Static page builder.

Reads YAML data files from data/ and writes one HTML page per file
to <slug>/index.html.

Each offer renders as a single self-contained paragraph that opens with
the venue name and the offer name, so that any fragment of the text
remains attributable to the correct offer when processed downstream.

The page carries no room rates (docs/kms_sync_notion_spec v2): the place
where a price used to sit holds a fixed sentence pointing to the booking
link, and the finished page is checked by price_guard before it is
written. A hit fails the build so that no price is ever published.

內容更新日 is the date the page content last changed: when the new page
equals the current one apart from that date, the old date is kept.

Usage:
    python scripts/build.py
    python scripts/build.py --style list

Requires: pip install pyyaml
"""

import argparse
import html
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("Missing dependency. Run: pip install pyyaml")

from price_guard import find_price


# "narrative" -> one flowing paragraph per offer (default)
# "list"      -> heading plus <ul><li>, kept for comparison only
DEFAULT_STYLE = "narrative"

# Stands where the reference price used to be.
PRICE_SENTENCE = "房價依入住日期與房型浮動，請以訂房連結即時報價為準"

# Taiwan has no DST, so a fixed offset is exact.
TAIPEI = timezone(timedelta(hours=8))

UPDATED_LABEL = "內容更新日："
UPDATED_RE = re.compile(re.escape(UPDATED_LABEL) + r"([0-9.]+)")

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
hr { border: 0; border-top: 1px solid #eee; margin: 2.5rem 0 1.5rem; }
"""


def esc(value):
    return html.escape(str(value or "").strip())


def validity_phrase(offer):
    end = str(offer.get("valid_to") or "").strip()
    if not end:
        raise ValueError(f"offer {offer.get('id')}: valid_to is required")
    note = str(offer.get("valid_note") or "").strip()
    text = f"販售至 {end}"
    if note:
        text += f"（{note}）"
    return text


def hashtag_suffix(offer):
    """Space-separated "#tag" run appended to the end of the narrative.

    Kept inside the narrative paragraph rather than emitted as its own
    block, so that a chunker splitting on block boundaries cannot detach
    the tags from the offer they describe.
    """
    tags = [
        str(t).strip().lstrip("#")
        for t in (offer.get("hashtags") or [])
        if str(t).strip()
    ]
    return " ".join(f"#{t}" for t in tags)


def narrative_sentence(venue, offer):
    """The plain-text paragraph for one offer, as rendered and measured."""
    body = "，".join(str(x).strip() for x in (offer.get("detail") or []) if str(x).strip())

    url = str(offer.get("booking_url") or "").strip()
    # The URL is half-width content, so it is delimited by spaces on both
    # sides. Brackets are avoided here because a full-width bracket sitting
    # flush against the URL leaves no boundary for link autodetection.
    link_clause = f"，訂房連結 {url} " if url else ""

    # The tag run is half-width content, so it is separated from the
    # closing full-width period by one half-width space.
    tags = hashtag_suffix(offer)
    tag_clause = f" {tags}" if tags else ""

    return (
        f"{venue}「{offer['name_zh']}」{link_clause}，"
        f"{PRICE_SENTENCE}，"
        + (f"{body}，" if body else "")
        + f"{validity_phrase(offer)}。{tag_clause}"
    )


def booking_link(offer, url):
    """<p> with the booking link, labelled "<offer name> 線上訂房".

    The space follows the punctuation rules: kept after a half-width
    ending (PASS), dropped after Chinese or full-width punctuation (）).
    """
    name = str(offer.get("name_zh") or "").strip()
    sep = " " if name[-1:].isascii() else ""
    return f'  <p><a href="{esc(url)}">{esc(name)}{sep}線上訂房</a></p>'


def render_narrative(venue, offer, disclaimer):
    """One offer, one paragraph, opening with venue and offer name.

    A heading is emitted purely as a visual anchor for human maintenance.
    The paragraph repeats the venue and offer name so that the text stays
    self-describing even when the heading is not carried along.
    """
    url = str(offer.get("booking_url") or "").strip()
    sentence = narrative_sentence(venue, offer)

    parts = [
        f"  <h2>{esc(offer['name_zh'])}</h2>",
        f"  <p>{esc(sentence)}</p>",
    ]
    if disclaimer:
        parts.append(f'  <p class="note">{esc(disclaimer)}</p>')
    if url:
        parts.append(booking_link(offer, url))

    return "<section>\n" + "\n".join(parts) + "\n</section>"


def render_list(venue, offer, disclaimer):
    """Heading plus bullet list. Retained for side-by-side comparison."""
    items = [esc(x) for x in (offer.get("detail") or []) if str(x).strip()]
    rows = "\n".join(f"    <li>{i}</li>" for i in items)

    parts = [
        f"  <h2>{esc(venue)}「{esc(offer['name_zh'])}」</h2>",
        f"  <p>{esc(PRICE_SENTENCE)}</p>",
        f"  <ul>\n{rows}\n  </ul>",
        f"  <p>{esc(validity_phrase(offer))}</p>",
    ]
    if disclaimer:
        parts.append(f'  <p class="note">{esc(disclaimer)}</p>')

    url = str(offer.get("booking_url") or "").strip()
    if url:
        parts.append(booking_link(offer, url))

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
        lengths.append((offer.get("id"), len(narrative_sentence(venue, offer))))

    if not sections:
        sections.append("<section>\n  <p>內容準備中。</p>\n</section>")

    body = "\n\n".join(sections)

    title = esc(prop.get("page_title") or venue)
    description = esc(prop.get("page_description"))

    def page_for(updated):
        return f"""<!DOCTYPE html>
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

{body}
<hr>
<p class="note">{UPDATED_LABEL}{updated}</p>
</body>
</html>
"""

    # Last line of defence: no price-like text may reach the page. URLs
    # are left out of the check; they carry rate codes, not amounts.
    visible = re.sub(r"https?://\S+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page_for(""))))
    leak = find_price(visible)
    if leak:
        raise ValueError(f"price-like text on the page: {leak!r}")

    out_path = Path(slug) / "index.html"
    old_page = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
    m = UPDATED_RE.search(old_page)
    if m and page_for(m.group(1)) == old_page:
        page = old_page
        state = f"unchanged since {m.group(1)}"
    else:
        updated = datetime.now(TAIPEI).strftime("%Y.%m.%d")
        page = page_for(updated)
        state = f"content changed, 內容更新日 {updated}"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")

    report = ", ".join(f"#{i}:{n}" for i, n in lengths)
    print(f"{data_file} -> {out_path} [{style}] {len(sections)} sections, {state}")
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
