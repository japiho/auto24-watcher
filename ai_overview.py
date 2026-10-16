#!/usr/bin/env python3
"""
Prepend an AI "Top 3 picks" overview to report.md.

Sends today's matching listings (state/listings.json) to Claude and asks for the
three best buys, weighing price vs. age/mileage, the known reliability and repair
costs of each engine/gearbox generation, drive type, and price history.

Env: ANTHROPIC_API_KEY (required), CLAUDE_MODEL (default claude-haiku-5-5, as in nexara).
Never fails the job: on any error the report is left as it is.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state" / "listings.json"
REPORT = ROOT / "report.md"
MODEL = os.environ.get("CLAUDE_MODEL") or "claude-haiku-5-5"
LOG = ROOT / "debug" / "ai_overview.log"


def log(msg: str) -> None:
    print(msg, file=sys.stderr)
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(msg + "\n")

SYSTEM = """You are a frank, experienced Estonian used-car advisor helping one buyer choose between \
listings from his saved searches. You know the common faults, repair costs and \
reliability records of specific engines, gearboxes and model generations \
(e.g. W205/S206 C-class, G21/G31 BMW, B8 Superb, BS/BT Outback, B8 Passat/Arteon), \
and typical parts/labour costs in Estonia.

Rules:
- Only use facts from the listing data plus well-established model/engine knowledge. \
Never invent options, service history or condition.
- Prefer: low running/repair costs, proven engines/gearboxes, lower mileage for the year, \
fair price for year+km+power, price drops or long time on market (negotiation room).
- Be cautious about: plug-in hybrids (battery/complexity), known weak engines or gearboxes, \
very high power for the price (hard driving), unknown drive type in a 4x4 search.
- If two rows are the same car on different sites (same year and mileage), treat it as one car \
and mention the cheapest site.
- Write concise English. No preamble, no closing remarks."""

INSTRUCTIONS = """Today is {today}. Below are all {n} cars currently matching the buyer's searches \
(one JSON object per line). Pick the TOP 3 best buys overall.

Output exactly this Markdown, nothing else:

## 🤖 Top 3 picks
_AI overview ({model}) from listing data and general model knowledge — not an inspection. \
Always check service history, a carVertical/ARK report and a pre-purchase inspection._

1. **[<title>](<url>)** — **<price>** · <year> · <km> · <drive>
   - **Why:** <one sentence: value vs. age/mileage/power, price drop or days listed>
   - **Running & repair costs:** <one sentence on known reliability/typical costs of this engine/gearbox>
   - **Check before buying:** <one sentence: the specific known weak points to inspect>

2. ...

3. ...

**Also consider:** <one line naming 1–2 runner-ups with a reason each, as links>

Listings:
{rows}"""


def rows_for_prompt(listings: dict, today: str) -> list[str]:
    cur = [v for v in listings.values() if v.get("last_seen") == today]
    out = []
    for v in sorted(cur, key=lambda x: (x.get("searches") or [""])[0]):
        hist = v.get("price_history") or []
        first_price = hist[0][1] if hist and hist[0][1] else v.get("price")
        try:
            days = (datetime.fromisoformat(today) - datetime.fromisoformat(v.get("first_seen", today))).days
        except ValueError:
            days = 0
        out.append(json.dumps({
            "search": ", ".join(v.get("searches") or []),
            "site": v.get("source"), "title": v.get("title"), "url": v.get("url"),
            "price_eur": v.get("price"),
            "price_change_since_first_seen": (v.get("price") or 0) - (first_price or 0),
            "days_tracked": days,
            "year": v.get("year"), "km": v.get("mileage"), "kw": v.get("power_kw"),
            "fuel": v.get("fuel"), "drive": v.get("drive") or "unknown",
            "body": v.get("body"), "seller": v.get("seller"),
            "reserved": v.get("reserved", False),
        }, ensure_ascii=False))
    return out


def main() -> int:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("ANTHROPIC_API_KEY not set — skipping AI overview")
        return 0
    force = "--force" in sys.argv          # dry runs: overview even on a no-change day
    if not STATE.exists() or (not REPORT.exists() and not force):
        print("No report or state — skipping AI overview")
        return 0
    if not REPORT.exists():
        REPORT.write_text("_No changes today._\n", encoding="utf-8")
    today = datetime.now(ZoneInfo("Europe/Tallinn")).date().isoformat()
    listings = json.loads(STATE.read_text(encoding="utf-8"))
    # watcher stamps last_seen with the UTC date; accept either
    utc_today = datetime.utcnow().date().isoformat()
    rows = rows_for_prompt(listings, utc_today) or rows_for_prompt(listings, today)
    if len(rows) < 2:
        log(f"Too few listings for an overview (utc={utc_today}, local={today}, rows={len(rows)})")
        return 0

    prompt = INSTRUCTIONS.format(today=today, n=len(rows), model=MODEL, rows="\n".join(rows))
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": MODEL, "max_tokens": 1500, "system": SYSTEM,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=120,
        )
        if r.status_code >= 400:
            log(f"API HTTP {r.status_code}: {r.text[:800]}")
        r.raise_for_status()
        data = r.json()
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
    except Exception as e:  # noqa: BLE001 — never break the daily report
        log(f"AI overview failed: {e!r}")
        return 0
    if "Top 3" not in text:
        log("Unexpected AI output — not added:\n" + text[:1500] + f"\nstop_reason={data.get('stop_reason')}")
        return 0

    REPORT.write_text(text + "\n\n---\n\n" + REPORT.read_text(encoding="utf-8"), encoding="utf-8")
    u = data.get("usage", {})
    log(f"AI overview added ({MODEL}, {u.get('input_tokens')} in / {u.get('output_tokens')} out tokens)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
