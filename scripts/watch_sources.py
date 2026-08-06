#!/usr/bin/env python3
"""
Source change watcher.

Opens each configured source page in a headless browser, extracts the
visible text, and compares it against the stored snapshot. Prints a
report and exits with code 1 when any source has changed, so that the
calling workflow can act on it.

The script never edits data files or pages. Its only job is to tell a
human that something upstream moved.

Config lives in sources.yml next to this script.

Usage:
    python scripts/watch_sources.py
    python scripts/watch_sources.py --init     # first run, store baselines
"""

import argparse
import difflib
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("Missing dependency. Run: pip install pyyaml")

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("Missing dependency. Run: pip install playwright && playwright install chromium")


ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = ROOT / "snapshots"
CONFIG = Path(__file__).resolve().parent / "sources.yml"

# Lines matching these are dropped before comparison. They are page
# furniture that changes on its own and would otherwise cause false alarms.
NOISE = [
    re.compile(r"^\s*$"),
    re.compile(r"^©"),
    re.compile(r"^\d{4}\s*(年|/)"),
]


def out(text=""):
    sys.stdout.buffer.write((str(text) + "\n").encode("utf-8"))


def normalise(raw):
    """Collapse the page text into a stable, comparable list of lines."""
    lines = []
    for line in raw.splitlines():
        line = " ".join(line.split())
        if not line:
            continue
        if any(p.match(line) for p in NOISE):
            continue
        lines.append(line)
    return lines


def fetch(page, url, settle_ms):
    resp = page.goto(url, wait_until="networkidle", timeout=60000)
    status = resp.status if resp else None
    page.wait_for_timeout(settle_ms)
    return status, page.inner_text("body")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--init", action="store_true",
                        help="store current pages as the baseline, report nothing")
    args = parser.parse_args()

    if not CONFIG.exists():
        sys.exit(f"Config not found: {CONFIG}")

    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    sources = config.get("sources") or []
    settle_ms = int(config.get("settle_ms", 5000))

    if not sources:
        sys.exit("No sources configured.")

    SNAPSHOT_DIR.mkdir(exist_ok=True)

    changed = []
    failed = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 2400}, locale="zh-TW")
        try:
            for src in sources:
                key = src["key"]
                label = src.get("label", key)
                url = src["url"]
                snap_path = SNAPSHOT_DIR / f"{key}.txt"

                try:
                    status, raw = fetch(page, url, settle_ms)
                except Exception as err:
                    failed.append((label, f"讀取失敗：{err}"))
                    continue

                if status != 200:
                    failed.append((label, f"HTTP {status}"))
                    continue

                lines = normalise(raw)

                # A page that suddenly returns almost nothing is far more
                # likely to be a loading failure than a real content wipe.
                if len(lines) < int(src.get("min_lines", 5)):
                    failed.append((label, f"內容過少（{len(lines)} 行），可能未載入完成"))
                    continue

                current = "\n".join(lines)

                if args.init or not snap_path.exists():
                    snap_path.write_text(current + "\n", encoding="utf-8")
                    out(f"[基準已建立] {label}（{len(lines)} 行）")
                    continue

                previous = snap_path.read_text(encoding="utf-8").splitlines()
                if previous == lines:
                    out(f"[無異動] {label}")
                    continue

                diff = list(difflib.unified_diff(
                    previous, lines, lineterm="", n=1,
                    fromfile="上次", tofile="本次",
                ))
                changed.append((label, url, diff))
                snap_path.write_text(current + "\n", encoding="utf-8")
        finally:
            browser.close()

    out()
    if failed:
        out("=" * 52)
        out("讀取異常")
        out("=" * 52)
        for label, reason in failed:
            out(f"  {label}：{reason}")
        out()

    if changed:
        out("=" * 52)
        out("偵測到異動")
        out("=" * 52)
        for label, url, diff in changed:
            out(f"\n## {label}")
            out(url)
            out("```diff")
            for line in diff:
                out(line)
            out("```")
        out()
        out("請人工核實後再更新資料檔，勿直接沿用上方差異內容。")

    if not changed and not failed:
        out("全部無異動。")

    # Non-zero exit tells the workflow that a human needs to look.
    sys.exit(1 if (changed or failed) else 0)


if __name__ == "__main__":
    main()
