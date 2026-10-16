#!/usr/bin/env python3
"""
Email report.md as a formatted HTML email.

Environment (set as GitHub repository secrets):
  SMTP_USER      sender address, e.g. you@gmail.com
  SMTP_PASSWORD  Gmail *app password* (not your normal password)
  EMAIL_TO       optional, comma-separated recipients (default: SMTP_USER)
  SMTP_HOST      optional, default smtp.gmail.com
  SMTP_PORT      optional, default 465 (SSL)
"""

import os
import re
import smtplib
import sys
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

import markdown

report = Path(sys.argv[1] if len(sys.argv) > 1 else "report.md").read_text(encoding="utf-8")
user = os.environ["SMTP_USER"]
password = os.environ["SMTP_PASSWORD"]
to = [a.strip() for a in (os.environ.get("EMAIL_TO") or user).split(",") if a.strip()]
host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
port = int(os.environ.get("SMTP_PORT") or 465)
issue_url = os.environ.get("ISSUE_URL", "")

today = datetime.now(ZoneInfo("Europe/Tallinn")).strftime("%d.%m.%Y")
new = re.search(r"New \((\d+)\)", report)
chg = re.search(r"Price changes \((\d+)\)", report)
rem = re.search(r"Removed[^(]*\((\d+)\)", report)
parts = [f"{new.group(1)} new" if new else "", f"{chg.group(1)} price changes" if chg else "",
         f"{rem.group(1)} removed" if rem else ""]
summary = ", ".join(p for p in parts if p) or ("baseline" if "Baseline" in report else "update")

body_html = markdown.markdown(report, extensions=["sane_lists"])
link = f'<p><a href="{issue_url}">Open on GitHub</a></p>' if issue_url else ""
html = f"""<!doctype html><html><body style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;
font-size:14px;line-height:1.5;color:#1f2328;max-width:760px">
{body_html}{link}
<p style="color:#8b949e;font-size:12px">Car watcher · github.com/japiho/auto24-watcher</p>
</body></html>"""

msg = EmailMessage()
msg["Subject"] = f"Autod {today}: {summary}"
msg["From"] = f"Car watcher <{user}>"
msg["To"] = ", ".join(to)
msg.set_content(report + (f"\n\n{issue_url}" if issue_url else ""))
msg.add_alternative(html, subtype="html")

with smtplib.SMTP_SSL(host, port, timeout=60) as s:
    s.login(user, password)
    s.send_message(msg)
print(f"Email sent to {', '.join(to)}: {msg['Subject']}")
