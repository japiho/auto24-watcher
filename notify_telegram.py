#!/usr/bin/env python3
"""Send report.md to a Telegram chat. Needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."""

import os
import re
import sys
from pathlib import Path

import requests

token = os.environ["TELEGRAM_BOT_TOKEN"]
chat_id = os.environ["TELEGRAM_CHAT_ID"]
text = Path(sys.argv[1] if len(sys.argv) > 1 else "report.md").read_text(encoding="utf-8")


def md_to_html(md: str) -> str:
    """Convert the small Markdown subset used in report.md to Telegram HTML."""
    s = md.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"~~(.+?)~~", r"<s>\1</s>", s)
    s = re.sub(r"^### (.+)$", r"<b>\1</b>", s, flags=re.M)
    s = re.sub(r"^- ", "• ", s, flags=re.M)
    return s


html = md_to_html(text)
# Telegram limit is 4096 chars per message; split on line boundaries.
chunks, cur = [], ""
for ln in html.splitlines(keepends=True):
    if len(cur) + len(ln) > 3900:
        chunks.append(cur)
        cur = ""
    cur += ln
if cur.strip():
    chunks.append(cur)

for c in chunks:
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": c, "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=30,
    )
    r.raise_for_status()
print(f"Sent {len(chunks)} Telegram message(s).")
