#!/usr/bin/env python3
"""
Email report.md as a formatted HTML email.

Environment:
  SMTP_HOST         e.g. mail.veebimajutus.ee
  SMTP_PORT         465 = implicit SSL, otherwise STARTTLS (same rule as nexara's SmtpEmailService)
  SMTP_USER         login, e.g. info@kergeraskus.ee
  SMTP_PASSWORD     secret
  SMTP_FROM         optional sender address (default: SMTP_USER)
  SMTP_SENDER_NAME  optional sender name (default: "Car watcher")
  EMAIL_TO          comma-separated recipients (default: SMTP_USER)
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
host = os.environ.get("SMTP_HOST") or "mail.veebimajutus.ee"
sender = os.environ.get("SMTP_FROM") or user
sender_name = os.environ.get("SMTP_SENDER_NAME") or "Car watcher"
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
msg["From"] = f"{sender_name} <{sender}>"
msg["To"] = ", ".join(to)
msg.set_content(report + (f"\n\n{issue_url}" if issue_url else ""))
msg.add_alternative(html, subtype="html")

if port == 465:
    conn = smtplib.SMTP_SSL(host, port, timeout=60)
else:
    conn = smtplib.SMTP(host, port, timeout=60)
    conn.starttls()
with conn as s:
    s.login(user, password)
    s.send_message(msg)
print(f"Email sent to {', '.join(to)}: {msg['Subject']}")
