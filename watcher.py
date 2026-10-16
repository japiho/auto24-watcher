#!/usr/bin/env python3
"""
Used-car search watcher for Estonian sites (autoportaal.ee, autodiiler.ee, auto24.ee).

Runs every search in searches.json, compares with the previous snapshot
(state/listings.json) and writes report.md with new listings, price changes and
removed listings. Cars listed on several sites are flagged.

Exit codes: 0 = ran (report.md only written when something changed),
            1 = every enabled source failed.
A single source failing doesn't stop the others; it is reported in report.md.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from sources import SOURCES, ScrapeError

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "searches.json"
STATE_FILE = ROOT / "state" / "listings.json"
REPORT_FILE = ROOT / "report.md"

SITE_NAMES = {"autoportaal": "autoportaal", "autodiiler": "autodiiler", "auto24": "auto24"}


# --------------------------------------------------------------------------- #
def eur(n) -> str:
    return "?" if n is None else f"{n:,}".replace(",", " ") + " €"


def km(n) -> str:
    return "?" if n is None else f"{n:,}".replace(",", " ") + " km"


def twin_key(v: dict):
    """Same physical car on two sites: same year and exact odometer reading."""
    if v.get("year") and v.get("mileage"):
        return (v["year"], v["mileage"])
    return None


def also_on(v: dict, current: dict) -> str:
    k = twin_key(v)
    if not k:
        return ""
    others = sorted({
        o["source"] for o in current.values()
        if o["source"] != v["source"] and twin_key(o) == k
    })
    return f" · _also on {', '.join(others)}_" if others else ""


def line(v: dict, current: dict) -> str:
    bits = [str(v.get("year") or "?"), km(v.get("mileage"))]
    if v.get("power_kw"):
        bits.append(f"{v['power_kw']} kW")
    if v.get("fuel"):
        bits.append(v["fuel"])
    reserved = " · **broneeritud**" if v.get("reserved") else ""
    return (f"`{v['source']}` [{v['title']}]({v['url']}) — **{eur(v.get('price'))}** · "
            + " · ".join(bits) + reserved + also_on(v, current))


# --------------------------------------------------------------------------- #
def diff(old: dict, new: dict, today: str, ok_sources: set[str]):
    added, changed, removed = [], [], []
    merged: dict[str, dict] = {}

    for key, cur in new.items():
        prev = old.get(key)
        if prev is None:
            cur["first_seen"] = today
            cur["price_history"] = [[today, cur["price"]]]
            added.append(cur)
        else:
            cur["first_seen"] = prev.get("first_seen", today)
            hist = prev.get("price_history") or [[cur["first_seen"], prev.get("price")]]
            if cur["price"] is not None and prev.get("price") is not None and cur["price"] != prev["price"]:
                changed.append((prev, cur))
                hist = hist + [[today, cur["price"]]]
            cur["price_history"] = hist
        cur["last_seen"] = today
        merged[key] = cur

    for key, prev in old.items():
        if key in new:
            continue
        if prev.get("source") in ok_sources:
            removed.append(prev)          # source worked and the car is gone
        else:
            merged[key] = prev            # source failed today — keep it, don't call it sold
    return added, changed, removed, merged


def build_report(added, changed, removed, current, failures, today) -> str:
    per_site = {}
    for v in current.values():
        per_site[v["source"]] = per_site.get(v["source"], 0) + 1
    sites = ", ".join(f"{SITE_NAMES.get(s, s)} {n}" for s, n in sorted(per_site.items()))
    out = [f"**{len(current)}** listings tracked ({sites}) — {today}", ""]

    if failures:
        out.append("⚠️ **Not checked today:** " + "; ".join(failures))
        out.append("")
    if added:
        out.append(f"### 🆕 New listings ({len(added)})")
        out += [f"- {line(v, current)}" for v in sorted(added, key=lambda x: x.get("price") or 0)]
        out.append("")
    if changed:
        out.append(f"### 💶 Price changes ({len(changed)})")
        for prev, cur in sorted(changed, key=lambda pc: cur_delta(pc)):
            d = cur_delta((prev, cur))
            arrow = "🔻" if d < 0 else "🔺"
            out.append(
                f"- {arrow} `{cur['source']}` [{cur['title']}]({cur['url']}): {eur(prev['price'])} → "
                f"**{eur(cur['price'])}** ({'+' if d > 0 else '−'}{eur(abs(d))}) · "
                f"{cur.get('year') or '?'} · {km(cur.get('mileage'))}"
            )
        out.append("")
    if removed:
        out.append(f"### ❌ Removed / sold ({len(removed)})")
        for v in removed:
            days = ""
            if v.get("first_seen"):
                try:
                    d0 = datetime.fromisoformat(v["first_seen"]).date()
                    days = f" · listed ~{(datetime.fromisoformat(today).date() - d0).days} days"
                except ValueError:
                    pass
            out.append(f"- `{v['source']}` ~~{v['title']}~~ — last {eur(v.get('price'))}{days} ([link]({v['url']}))")
        out.append("")
    return "\n".join(out)


def cur_delta(pc) -> int:
    return pc[1]["price"] - pc[0]["price"]


# --------------------------------------------------------------------------- #
def main() -> int:
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    searches = [s for s in cfg["searches"] if s.get("enabled", True)]
    today = datetime.now(timezone.utc).date().isoformat()

    session = requests.Session()
    current: dict[str, dict] = {}
    ok_sources: set[str] = set()
    failures: list[str] = []

    for s in searches:
        src = s["source"]
        print(f"[{s['name']}] {src}: {s['url']}")
        try:
            got = SOURCES[src](session, s)
        except (ScrapeError, requests.RequestException, ValueError, KeyError) as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            failures.append(f"{s['name']} ({e})")
            continue
        print(f"  {len(got)} matching listings")
        for v in got.values():
            v["search"] = s["name"]
        current.update(got)
        ok_sources.add(src)
        time.sleep(2)

    if not ok_sources:
        print("ERROR: every source failed", file=sys.stderr)
        return 1

    first_run = not STATE_FILE.exists()
    old = {} if first_run else json.loads(STATE_FILE.read_text(encoding="utf-8"))

    # Guard: a source that suddenly returns far fewer cars is more likely broken
    # than sold out. Treat it as failed for today.
    for src in list(ok_sources):
        before = sum(1 for v in old.values() if v.get("source") == src)
        now = sum(1 for v in current.values() if v["source"] == src)
        if before >= 6 and now < 0.5 * before:
            print(f"ERROR: {src} returned {now} vs {before} before — ignoring it today", file=sys.stderr)
            failures.append(f"{src} (only {now} listings vs {before} before — site change?)")
            ok_sources.discard(src)
            current = {k: v for k, v in current.items() if v["source"] != src}

    added, changed, removed, merged = diff(old, current, today, ok_sources)

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(merged, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")

    if first_run:
        body = "\n".join(f"- {line(v, current)}" for v in sorted(current.values(), key=lambda x: x.get("price") or 0))
        warn = ("\n\n⚠️ **Not checked:** " + "; ".join(failures)) if failures else ""
        REPORT_FILE.write_text(
            f"First run — baseline saved with **{len(current)}** listings. "
            f"From now on you'll get new listings and price changes here.{warn}\n\n{body}\n",
            encoding="utf-8",
        )
    elif added or changed or removed or failures:
        REPORT_FILE.write_text(build_report(added, changed, removed, merged, failures, today), encoding="utf-8")
    else:
        print("No changes.")
        REPORT_FILE.unlink(missing_ok=True)

    print(f"new={len(added)} changed={len(changed)} removed={len(removed)} total={len(merged)} failed={len(failures)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
