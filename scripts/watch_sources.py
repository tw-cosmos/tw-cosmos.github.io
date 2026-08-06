#!/usr/bin/env python3
"""
Source change watcher.

Opens each configured source page in a headless browser, extracts the
visible text, and compares it against the stored snapshot. Prints a
report and exits with code 1 when any source has changed, so that the
calling workflow can act on it.

The script never edits data files or pages. Its only job is to tell a
human that something upstream moved.

Loading is confirmed by stability, not by a fixed delay and not by an
expected item count: the page is polled until two consecutive reads are
identical. A fixed sleep is unreliable because runner speed varies, and a
half-loaded capture produces a snapshot that reports phantom changes next
week. An expected count would be worse still, because the count itself is
what we are watching for -- an offer being added or withdrawn upstream is
the signal, not an error.

Config lives in sources.yml next to this script.

Usage:
    python scripts/watch_sources.py
    python scripts/watch_sources.py --init     # rebuild baselines
"""

import argparse
import difflib
import re
import sys
import time
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


def scroll_through(page, poll_ms, max_steps=40):
    """Walk the page from top to bottom, then back to the top.

    Content below the fold is often rendered only once it enters the
    viewport. A headless run stays where it lands, so without this the
    capture silently omits whatever never scrolled into view -- which
    looks identical to those items having been withdrawn upstream.
    """
    step = page.viewport_size["height"] if page.viewport_size else 800
    position = 0
    for _ in range(max_steps):
        page.mouse.wheel(0, step)
        page.wait_for_timeout(400)
        height = page.evaluate("document.body.scrollHeight")
        position += step
        if position >= height:
            break
    # Let anything triggered by the last step finish, then return to the
    # top so the captured text is in document order.
    page.wait_for_timeout(poll_ms)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(400)


def load_page(page, src, poll_ms, timeout_ms):
    """Open the page and wait until its text stops changing.

    Returns (status, text, settled). settled is False when the text never
    stabilised, which the caller treats as a failed read rather than as a
    content change, so a partial capture never overwrites a good snapshot.
    """
    resp = page.goto(src["url"], wait_until="networkidle", timeout=60000)
    status = resp.status if resp else None
    if status != 200:
        return status, "", False

    scroll_through(page, poll_ms)

    # Presence check only. It answers "did anything load at all", never
    # "did the right number of things load".
    pattern = src.get("expect_pattern")
    regex = re.compile(pattern, re.I) if pattern else None

    deadline = time.monotonic() + timeout_ms / 1000
    previous = None
    reads = 0

    while time.monotonic() < deadline:
        page.wait_for_timeout(poll_ms)
        text = page.inner_text("body")
        reads += 1

        if text == previous:
            # Two identical consecutive reads: the page has settled.
            if regex and not regex.search(text):
                out("    頁面已穩定但未出現預期內容，視為載入失敗")
                return status, text, False
            return status, text, True

        previous = text

    out(f"    等待逾時：內容持續變動（已讀取 {reads} 次）")
    return status, previous or "", False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--init", action="store_true",
                        help="store current pages as the baseline, report nothing")
    args = parser.parse_args()

    if not CONFIG.exists():
        sys.exit(f"Config not found: {CONFIG}")

    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    sources = config.get("sources") or []
    poll_ms = int(config.get("poll_ms", 2000))
    timeout_ms = int(config.get("load_timeout_ms", 45000))

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
                snap_path = SNAPSHOT_DIR / f"{key}.txt"
                out(f"讀取 {label} ...")

                try:
                    status, raw, settled = load_page(page, src, poll_ms, timeout_ms)
                except Exception as err:
                    failed.append((label, f"讀取失敗：{err}"))
                    continue

                if status != 200:
                    failed.append((label, f"HTTP {status}"))
                    continue

                if not settled:
                    # Never overwrite a good baseline with a partial read.
                    failed.append((label, "頁面未完整載入，本次不比對、不更新快照"))
                    continue

                lines = normalise(raw)

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
                changed.append((label, src["url"], diff))
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

    sys.exit(1 if (changed or failed) else 0)


if __name__ == "__main__":
    main()
