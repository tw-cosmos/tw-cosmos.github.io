#!/usr/bin/env python3
"""
Telegram notice for the Notion sync workflow (docs/kms_sync_notion_spec
v2, section 4).

    change   the page changed: list added / removed / modified offers,
             comparing data/taipei-garden.yml at --before with the
             working copy
    failure  the workflow failed

Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from the environment. When
either is missing it prints "telegram skipped" and exits 0, so the
workflow still completes. A failed send is reported as a warning and
does not fail the job either.

The Actions log is public: the token is part of the API URL, so neither
the URL nor exception text that may contain it is ever printed.

Usage:
    python scripts/notify_telegram.py change --before <commit>
    python scripts/notify_telegram.py failure

Requires: pyyaml (change only)
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

DATA_FILE = "data/taipei-garden.yml"
PAGE_URL = "https://tw-cosmos.github.io/taipei-garden/"

CHANGE_TITLE = "花園靜態頁內容有變動，需重匯 KMS"
FAILURE_TITLE = "⚠ 花園靜態頁同步失敗"

# Field groups reported for a modified offer, in display order.
FIELDS = [
    ("名稱", ("name_zh",)),
    ("檔期", ("valid_from", "valid_to", "valid_note")),
    ("摘要", ("summary",)),
    ("條件", ("tags",)),
    ("連結", ("booking_url",)),
]

TELEGRAM_LIMIT = 4000


# --------------------------------------------------------------------------
# Diff
# --------------------------------------------------------------------------

def load_offers(text):
    import yaml

    data = yaml.safe_load(text or "") or {}
    return {o["id"]: o for o in (data.get("projects") or []) if o.get("id")}


def old_yaml(rev):
    try:
        return subprocess.run(
            ["git", "show", f"{rev}:{DATA_FILE}"],
            check=True, capture_output=True, encoding="utf-8",
        ).stdout
    except subprocess.CalledProcessError:
        return ""


def diff_lines(before, after):
    added = [after[i] for i in after if i not in before]
    removed = [before[i] for i in before if i not in after]
    modified = []
    for i in after:
        if i not in before:
            continue
        changed = [
            label for label, keys in FIELDS
            if any(before[i].get(k) != after[i].get(k) for k in keys)
        ]
        if changed:
            modified.append((after[i], changed))

    lines = []
    if added:
        lines += ["", "新增："] + [f"- {o['name_zh']}" for o in added]
    if removed:
        lines += ["", "移除："] + [f"- {o['name_zh']}" for o in removed]
    if modified:
        lines += ["", "修改："] + [f"- {o['name_zh']}（{'、'.join(c)}）" for o, c in modified]
    if not lines:
        # The page changed without any listed field changing, e.g. the
        # fixed wording or the layout.
        lines = ["", "專案未變動，頁面固定文字或版面有更新"]
    return lines


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------

def send(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("telegram skipped (TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set)")
        return

    if len(text) > TELEGRAM_LIMIT:
        text = text[:TELEGRAM_LIMIT] + "\n…"
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps({
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
        print("telegram sent")
    except urllib.error.HTTPError as err:
        # Telegram's error body carries a description, never the token.
        try:
            desc = json.loads(err.read().decode("utf-8")).get("description", "")
        except ValueError:
            desc = ""
        print(f"::warning::telegram send failed: HTTP {err.code} {desc}")
    except Exception as err:  # noqa: BLE001 - message may hold the URL
        print(f"::warning::telegram send failed: {type(err).__name__}")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["change", "failure"])
    ap.add_argument("--before", metavar="REV", help="commit to compare against (change only)")
    ap.add_argument("--dry-run", action="store_true", help="print the message instead of sending")
    args = ap.parse_args()

    if args.kind == "change":
        if not args.before:
            sys.exit("change needs --before")
        with open(DATA_FILE, encoding="utf-8") as f:
            after = load_offers(f.read())
        before = load_offers(old_yaml(args.before))
        text = "\n".join([CHANGE_TITLE] + diff_lines(before, after) + ["", PAGE_URL])
    else:
        lines = [FAILURE_TITLE]
        server = os.environ.get("GITHUB_SERVER_URL")
        repo = os.environ.get("GITHUB_REPOSITORY")
        run_id = os.environ.get("GITHUB_RUN_ID")
        if server and repo and run_id:
            lines += ["", f"{server}/{repo}/actions/runs/{run_id}"]
        text = "\n".join(lines)

    if args.dry_run:
        sys.stdout.buffer.write((text + "\n").encode("utf-8"))
        return
    send(text)


if __name__ == "__main__":
    main()
