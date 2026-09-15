#!/usr/bin/env python3
"""
Notion -> data/taipei-garden.yml generator.

Reads the "Cosmos 官網專案每日監控" Notion database, keeps only the rows
that pass the verification gate, and rewrites data/taipei-garden.yml so
that the existing build (scripts/build.py) can render it unchanged.

Verification gate (all conditions must hold, see docs/kms_sync_notion_spec):
    來源 = 台北花園
    上架狀態 = 上架中
    發布狀態 = 已核實
    核實日 non-empty
    服務費 non-empty (已含 / 另加)
Rows that fail the gate are counted in the log and never rendered.
Rows without a usable price or without 檔期迄 are skipped with a log line.

The YAML is only written when at least one row passes. Any API failure or
an empty result leaves the existing file untouched and exits non-zero, so
the calling workflow fails instead of publishing an empty page.

Usage:
    python scripts/sync_notion.py                 # needs NOTION_TOKEN in env
    python scripts/sync_notion.py --from-json f   # offline test with a saved reply

Requires: pip install pyyaml requests
"""

import argparse
import json
import math
import os
import re
import sys
import unicodedata
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("Missing dependency. Run: pip install pyyaml")


# --------------------------------------------------------------------------
# Constants: Notion side
# --------------------------------------------------------------------------

DATA_SOURCE_ID = "d01cbb21-4857-49dc-8e29-3cfdc88aa2d4"
NOTION_VERSION = "2025-09-03"
NOTION_QUERY_URL = f"https://api.notion.com/v1/data_sources/{DATA_SOURCE_ID}/query"

SOURCE_VALUE = "台北花園"
LISTED_VALUE = "上架中"
PUBLISH_OK = "已核實"
PUBLISH_PENDING = "待審"

SERVICE_CHARGE_MAP = {
    "已含": "included",
    "另加": "excluded",
}

# Property names exactly as they appear in Notion.
P = {
    "name": "專案名稱",
    "source": "來源",
    "listed": "上架狀態",
    "publish": "發布狀態",
    "verified": "核實日",
    "service": "服務費",
    "price_site": "官網起價",
    "price_night": "每晚價",
    "from": "檔期起",
    "to": "檔期迄",
    "room": "房型",
    "meal": "餐食",
    "summary": "摘要",
    "tags": "標籤項",
    "booking": "訂房連結",
    "image": "圖片",
    "code": "內部代碼",
    "match_key": "比對鍵",
    "category": "分類",
    "property": "property",
}

# --------------------------------------------------------------------------
# Constants: YAML side (no Notion source, kept here)
# --------------------------------------------------------------------------

OUTPUT_FILE = Path("data") / "taipei-garden.yml"
HEADER_COMMENT = "# generated from Notion, do not edit\n"

PROPERTY_BLOCK = {
    "slug": "taipei-garden",
    "name_zh": "台北花園大酒店",
    "name_en": "Taipei Garden Hotel",
    "page_title": "台北花園大酒店、住宿專案",
    "page_description": "台北花園大酒店住宿專案資訊，含專案內容與適用期間。",
    "price_disclaimer": "價格為參考起始價，實際房價依訂房日期與房型浮動，請以線上訂房系統顯示為準。",
    "verified_source": "Notion 監控庫（核實閘）",
}

PRICE_UNIT = "每房每晚"
HASHTAGS = ["住房優惠", "住房專案", "訂房優惠"]
DEFAULT_PROPERTY_CODE = "twtai17118"
BOOKING_TEMPLATE = (
    "https://www.book-secure.com/index.php?s=results"
    "&property={property}&rate={rate}"
    "&advance=1-days&adults1=2&children1=0&locale=zh_Hant_HK"
)

# Tags that classify the offer rather than describe a condition; they are
# left out of the "適用條件" clause. Extend as new ones show up.
EXCLUDED_TAGS = {"官網專案", "官網優惠", "官網直訂", "官網為準", "官網限定"}

# A tag containing any of these goes into valid_note as well.
VALID_NOTE_MARKERS = ("不適用", "除外", "排除", "限")


# --------------------------------------------------------------------------
# Notion API
# --------------------------------------------------------------------------

def fetch_rows(token):
    try:
        import requests
    except ImportError:
        sys.exit("Missing dependency. Run: pip install requests")

    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    body = {
        "filter": {"property": P["source"], "select": {"equals": SOURCE_VALUE}},
        "page_size": 100,
    }
    rows = []
    cursor = None
    while True:
        if cursor:
            body["start_cursor"] = cursor
        resp = requests.post(NOTION_QUERY_URL, headers=headers, json=body, timeout=60)
        if resp.status_code != 200:
            sys.exit(f"Notion API error {resp.status_code}: {resp.text[:500]}")
        payload = resp.json()
        rows.extend(payload.get("results") or [])
        if not payload.get("has_more"):
            break
        cursor = payload.get("next_cursor")
        if not cursor:
            break
    return rows


# --------------------------------------------------------------------------
# Property extraction
# --------------------------------------------------------------------------

def _rich(parts):
    return "".join(p.get("plain_text") or "" for p in (parts or [])).strip()


def prop(page, key):
    """Return the plain value of a property, or None / [] when empty."""
    data = (page.get("properties") or {}).get(P[key])
    if not data:
        return None
    kind = data.get("type")
    if kind == "title":
        return _rich(data.get("title")) or None
    if kind == "rich_text":
        return _rich(data.get("rich_text")) or None
    if kind == "select":
        sel = data.get("select")
        return (sel or {}).get("name") or None
    if kind == "status":
        sel = data.get("status")
        return (sel or {}).get("name") or None
    if kind == "multi_select":
        return [x.get("name") for x in (data.get("multi_select") or []) if x.get("name")]
    if kind == "date":
        return ((data.get("date") or {}).get("start")) or None
    if kind == "number":
        return data.get("number")
    if kind == "url":
        return (data.get("url") or "").strip() or None
    if kind == "formula":
        f = data.get("formula") or {}
        return f.get(f.get("type"))
    return None


# --------------------------------------------------------------------------
# Text normalisation (CLAUDE.md punctuation rules)
# --------------------------------------------------------------------------

_CJK = "一-鿿㐀-䶿"
_FULLWIDTH_PUNCT = "，。、；：？！（）「」"
_SUFFIX_RE = re.compile(r"\s*[（(]\s*即日起[^()（）]*[)）]\s*$")


def strip_emoji(text):
    out = []
    for ch in text:
        if ch in ("️", "‍"):
            continue
        if unicodedata.category(ch) in ("So", "Sk", "Cs"):
            continue
        out.append(ch)
    return "".join(out)


def normalise(text):
    """Apply the punctuation rules that the hand-written YAML followed."""
    if text is None:
        return ""
    s = str(text)
    s = strip_emoji(s)
    s = s.replace("(", "（").replace(")", "）")
    s = s.replace("・", "、").replace("･", "、")
    s = s.replace("　", " ")
    # one half-width space between CJK and Latin letters / digits
    s = re.sub(rf"([{_CJK}])([A-Za-z0-9])", r"\1 \2", s)
    s = re.sub(rf"([A-Za-z0-9%])([{_CJK}])", r"\1 \2", s)
    # full-width punctuation sits flush against its neighbours
    s = re.sub(rf"\s*([{_FULLWIDTH_PUNCT}])\s*", r"\1", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


def clean_name(raw):
    s = _SUFFIX_RE.sub("", raw or "")
    return normalise(s)


def dot_date(iso):
    """2027-06-30 -> 2027.06.30 (date-only; time part dropped)."""
    if not iso:
        return ""
    return str(iso)[:10].replace("-", ".")


def slugify(text):
    s = re.sub(r"[^A-Za-z0-9]+", "-", str(text or "")).strip("-")
    return s or "row"


# --------------------------------------------------------------------------
# Row -> offer
# --------------------------------------------------------------------------

def gate(page, counts):
    """Return the reason a row is excluded, or None if it passes."""
    if prop(page, "source") != SOURCE_VALUE:
        counts["source_other"] += 1
        return "source"
    if prop(page, "listed") != LISTED_VALUE:
        counts["delisted"] += 1
        return "delisted"
    publish = prop(page, "publish")
    if publish != PUBLISH_OK:
        if publish == PUBLISH_PENDING:
            counts["pending"] += 1
        else:
            counts["publish_other"] += 1
        return "publish"
    if not prop(page, "verified"):
        counts["verified_missing"] += 1
        return "verified_missing"
    service = prop(page, "service")
    if not service:
        counts["service_charge_missing"] += 1
        return "service_charge_missing"
    if service not in SERVICE_CHARGE_MAP:
        counts["service_charge_unknown"] += 1
        return "service_charge_unknown"
    return None


def pick_price(page):
    """Return (price, source_label) or (None, None)."""
    site = prop(page, "price_site")
    if site not in (None, ""):
        return int(math.floor(float(site) + 0.5)), "官網起價"
    night = prop(page, "price_night")
    if night not in (None, ""):
        return int(math.floor(float(night) + 0.5)), "每晚價(四捨五入)"
    return None, None


def to_offer(page, log):
    code = prop(page, "code")
    if code:
        offer_id = code
    else:
        offer_id = slugify(prop(page, "match_key") or page.get("id"))
        log.append(f"  [{offer_id}] 內部代碼 empty, id derived from 比對鍵")

    name = clean_name(prop(page, "name"))
    if not name:
        log.append(f"  [{offer_id}] SKIP name missing")
        return None

    price, price_src = pick_price(page)
    if price is None:
        log.append(f"  [{offer_id}] SKIP price missing")
        return None

    valid_to = dot_date(prop(page, "to"))
    if not valid_to:
        log.append(f"  [{offer_id}] SKIP valid_to missing")
        return None

    tags = [normalise(t) for t in (prop(page, "tags") or [])]
    tags = [t for t in tags if t]
    condition_tags = [t for t in tags if t not in EXCLUDED_TAGS]
    note_tags = [t for t in condition_tags if any(m in t for m in VALID_NOTE_MARKERS)]

    summary = normalise(prop(page, "summary")).rstrip("。")
    detail = []
    if summary:
        detail.append(summary)
    if condition_tags:
        detail.append("適用條件：" + "、".join(condition_tags))

    property_code = prop(page, "property") or DEFAULT_PROPERTY_CODE
    if code:
        booking_url = BOOKING_TEMPLATE.format(property=property_code, rate=code)
    else:
        booking_url = prop(page, "booking") or ""
        log.append(f"  [{offer_id}] booking_url taken from Notion as-is (no 內部代碼)")

    log.append(f"  [{offer_id}] price {price} from {price_src}; valid_to {valid_to}; {name}")

    return {
        "id": offer_id,
        "name_zh": name,
        "name_en": "",
        "price_from": price,
        "price_unit": PRICE_UNIT,
        "service_charge": SERVICE_CHARGE_MAP[prop(page, "service")],
        "detail": detail,
        "valid_to": valid_to,
        "valid_note": "、".join(note_tags),
        "booking_url": booking_url,
        "hashtags": list(HASHTAGS),
        # reference fields, not rendered by build.py
        "valid_from": dot_date(prop(page, "from")),
        "category": prop(page, "category") or "",
        "room": prop(page, "room") or "",
        "meal": prop(page, "meal") or "",
        "summary": prop(page, "summary") or "",
        "tags": tags,
        "image": prop(page, "image") or "",
        "verified_date": dot_date(prop(page, "verified")),
        "price_source": price_src,
    }


def build_document(pages, log):
    counts = {
        "total": len(pages),
        "source_other": 0,
        "delisted": 0,
        "pending": 0,
        "publish_other": 0,
        "verified_missing": 0,
        "service_charge_missing": 0,
        "service_charge_unknown": 0,
    }
    passed = []
    for page in pages:
        if gate(page, counts) is None:
            passed.append(page)

    offers = []
    for page in passed:
        offer = to_offer(page, log)
        if offer:
            offers.append(offer)

    # 檔期迄 ascending; rows without it were skipped above. id as tiebreak.
    offers.sort(key=lambda o: (o["valid_to"], o["id"]))

    verified_dates = [o["verified_date"] for o in offers if o["verified_date"]]
    prop_block = dict(PROPERTY_BLOCK)
    verified_source = prop_block.pop("verified_source")
    prop_block["verified_date"] = max(verified_dates) if verified_dates else ""
    prop_block["verified_source"] = verified_source

    return {"property": prop_block, "projects": offers}, counts


def summary_lines(counts, produced):
    return [
        f"rows with 來源={SOURCE_VALUE}: {counts['total']}",
        f"  已下架: {counts['delisted']}",
        f"  發布狀態=待審: {counts['pending']}",
        f"  發布狀態 other/empty: {counts['publish_other']}",
        f"  核實日 missing: {counts['verified_missing']}",
        f"  服務費 missing: {counts['service_charge_missing']}",
        f"  服務費 unknown value: {counts['service_charge_unknown']}",
        f"  produced: {produced}",
    ]


def write_yaml(doc, path):
    text = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=4096)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(HEADER_COMMENT + text, encoding="utf-8", newline="\n")
    tmp.replace(path)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-json", metavar="FILE", help="read a saved API reply instead of calling Notion")
    ap.add_argument("--out", metavar="FILE", default=str(OUTPUT_FILE), help="output YAML path")
    args = ap.parse_args()

    if args.from_json:
        with open(args.from_json, encoding="utf-8") as f:
            pages = json.load(f)
        if isinstance(pages, dict):
            pages = pages.get("results") or []
    else:
        token = os.environ.get("NOTION_TOKEN", "").strip()
        if not token:
            sys.exit("NOTION_TOKEN is not set")
        pages = fetch_rows(token)

    log = []
    doc, counts = build_document(pages, log)
    produced = len(doc["projects"])

    for line in summary_lines(counts, produced):
        print(line)
    for line in log:
        print(line)

    if produced == 0:
        sys.exit("ERROR: no row passed the verification gate; YAML left untouched")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_yaml(doc, out_path)
    print(f"wrote {out_path} ({produced} projects, verified_date {doc['property']['verified_date']})")


if __name__ == "__main__":
    main()
