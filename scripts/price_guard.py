#!/usr/bin/env python3
"""
Price leak guard (docs/kms_sync_notion_spec v2, section 2).

The static page must carry no room-rate figures. Summaries are written
upstream by a model and may one day include a price, so every free-text
field is checked here before it is written out.

Discount ratios (9 折, 96 折), quantities (限量 3 間), dates and
percentages are offer rules, not prices, and must pass.

Usage:
    python scripts/price_guard.py          # run the self-test
"""

import re
import sys

# Full-width digits and dollar sign are folded to half-width before
# matching. The full-width comma is left alone and matched explicitly,
# since NFKC would also fold the ordinary Chinese comma.
_FOLD = str.maketrans({**{chr(0xFF10 + i): str(i) for i in range(10)}, "＄": "$"})

# 元 followed by these is a calendar word (元旦, 元宵, 元月, 元年), not currency.
_YUAN = r"元(?![旦宵月年])"

# A number followed by one of these counts something other than money
# (9 折, 2 晚, 1,500 點, 限量 3 間, 10%).
_NOT_MONEY = r"(?![\d,，.]|\s*(?:折|晚|人|間|位|名|份|張|次|日|天|歲|點|%|％))"

# Words that put an amount right after them.
_PRICE_WORDS = "房價|價格|售價|特價|優惠價|定價|加價|加收|現折|折抵|每人|只要|僅需|低至"

PRICE_RE = re.compile(
    "|".join([
        r"(?<![A-Za-z])NT\s*\$",
        r"(?<![A-Za-z])NT\s*\d",
        r"(?<![A-Za-z])(?:NTD|TWD)(?![A-Za-z])",
        r"新?[台臺]幣",
        r"\$\s*\d",
        r"\d\s*" + _YUAN,
        r"[一二兩三四五六七八九十百千萬]+\s*" + _YUAN,
        r"\d{3,}\s*[塊圓]",
        r"(?<![\d.])\d+(?:\.\d+)?\s*(?:千|萬(?!聖))",
        r"(?<!\d)\d{1,3}(?:[,，]\d{3})+" + _NOT_MONEY,
        r"\d{3,}\s*(?:起|[/／]\s*晚|每晚)",
        rf"(?:{_PRICE_WORDS})\s*[:：]?\s*\d+(?:[,，.]\d+)*" + _NOT_MONEY,
    ]),
    re.IGNORECASE,
)


def find_price(text):
    """Return the first price-like fragment in text, or None."""
    if not text:
        return None
    m = PRICE_RE.search(str(text).translate(_FOLD))
    return m.group(0) if m else None


# --------------------------------------------------------------------------
# self-test
# --------------------------------------------------------------------------

_MUST_HIT = [
    "參考起始價 NT$3,045 起",
    "NT $2908",
    "TWD 3999",
    "新台幣三千元",
    "每晚 3,999",
    "3，045 起",
    "加價 500 即可升等",
    "只要 1999",
    "2908 起",
    "１，２００元",
    "$120 per night",
    "500元折價券",
    "房價：2908",
    "NT3000",
    "3000 塊",
    "1.5 萬",
    "3 千",
    "現折 500",
    "每人 1500",
    "低至 2,999",
]

_MUST_PASS = [
    "專屬 Cosmos CLUB 會員的 9 折訂房優惠，含自助早餐",
    "持台灣高鐵或台鐵票根入住可享 96 折優惠",
    "每日限量 3 間且不得與其他優惠併用",
    "2026/12/30–2027/1/2 不適用",
    "已含 10% 服務費",
    "販售至 2027.06.30",
    "2026 元旦不適用",
    "每房 2 人",
    "下午三點至五點提供 Happy Hour 餐飲",
    "房價依入住日期與房型浮動，請以訂房連結即時報價為準",
    "本頁不載明房價。房價依入住日期與房型浮動，請以線上訂房頁面即時報價為準。",
    "多元體驗",
    "低至 7 折",
    "特價 9 折",
    "優惠價 85 折",
    "售價 8 折起",
    "只要 2 晚",
    "僅需 1 人",
    "點數 1,500 點",
    "限量 1,000 間",
    "12/31，100 人",
    "Frontdesk 24 小時服務",
    "2026 萬聖節限定",
    "贈 2 塊蛋糕",
    "每人 2 份早餐",
]


def _self_test():
    bad = []
    for s in _MUST_HIT:
        if not find_price(s):
            bad.append(f"MISSED  {s}")
    for s in _MUST_PASS:
        hit = find_price(s)
        if hit:
            bad.append(f"FALSE+  {s}  ({hit})")
    for line in bad:
        sys.stdout.buffer.write((line + "\n").encode("utf-8"))
    print(f"{len(_MUST_HIT)} hit cases, {len(_MUST_PASS)} pass cases, {len(bad)} failures")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_self_test())
