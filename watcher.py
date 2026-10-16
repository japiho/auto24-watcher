#!/usr/bin/env python3
"""
Used-car watcher for Estonian sites.

Runs every search in searches.json on every site, compares with the previous
snapshot (state/listings.json) and writes report.md:
new listings, price changes and removed listings — grouped by search, with the
drive type (veoskeem) on every row and cars listed on several sites flagged.

Exit codes: 0 = ran (report.md only when something changed or a site failed)
            1 = every site failed.
A failing site doesn't stop the others, and its cars are never marked as sold.
"""

from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import requests

from sources import SOURCES, ScrapeError

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "searches.json"
STATE_FILE = ROOT / "state" / "listings.json"
REPORT_FILE = ROOT / "report.md"

DRIVE_LABEL = {"esivedu": "Esivedu", "tagavedu": "Tagavedu", "nelikvedu": "**Nelikvedu**"}


# --------------------------------------------------------------------------- #
# formatting
# --------------------------------------------------------------------------- #
def eur(n):
    return "?" if n is None else f"{n:,}".replace(",", " ") + " €"


def km(n):
    return "?" if n is None else f"{n:,}".replace(",", " ") + " km"


def twin_key(v):
    return (v["year"], v["mileage"]) if v.get("year") and v.get("mileage") else None


def also_on(v, pool):
    k = twin_key(v)
    if not k:
        return ""
    others = sorted({o["source"] for o in pool.values() if o["source"] != v["source"] and twin_key(o) == k})
    return f" · _also on {', '.join(others)}_" if others else ""


def specs(v):
    bits = [str(v.get("year") or "?"), km(v.get("mileage"))]
    if v.get("power_kw"):
        bits.append(f"{v['power_kw']} kW")
    if v.get("fuel"):
        bits.append(v["fuel"])
    bits.append(DRIVE_LABEL.get(v.get("drive"), "veoskeem ?"))
    return " · ".join(bits)


def row(v, pool):
    reserved = " · **broneeritud**" if v.get("reserved") else ""
    return (f"- `{v['source']}` [{v['title']}]({v['url']}) — **{eur(v.get('price'))}** · "
            f"{specs(v)}{reserved}{also_on(v, pool)}")


def grouped(items, search_names, key=lambda v: v, sort=lambda v: v.get("price") or 0):
    """Yield (search name, [items]) in searches.json order."""
    for name in search_names:
        sel = [it for it in items if name in key(it).get("searches", [])]
        if sel:
            yield name, sorted(sel, key=lambda it: sort(key(it)))


# --------------------------------------------------------------------------- #
def diff(old, new, today, ok_sites):
    added, changed, removed, merged = [], [], [], {}
    for k, cur in new.items():
        prev = old.get(k)
        if prev is None:
            cur["first_seen"] = today
            cur["price_history"] = [[today, cur["price"]]]
            added.append(cur)
        else:
            cur["first_seen"] = prev.get("first_seen", today)
            hist = prev.get("price_history") or [[cur["first_seen"], prev.get("price")]]
            if cur.get("price") is not None and prev.get("price") is not None and cur["price"] != prev["price"]:
                changed.append((prev, cur))
                hist = hist + [[today, cur["price"]]]
            cur["price_history"] = hist
            for f in ("drive", "body", "power_kw"):          # keep details learned earlier
                if cur.get(f) is None and prev.get(f) is not None:
                    cur[f] = prev[f]
        cur["last_seen"] = today
        merged[k] = cur
    for k, prev in old.items():
        if k in new:
            continue
        if prev.get("source") in ok_sites:
            removed.append(prev)
        else:
            merged[k] = prev                                  # site failed today: keep
    return added, changed, removed, merged


def build_report(added, changed, removed, pool, failures, names, today, first_run):
    per_site = {}
    for v in pool.values():
        per_site[v["source"]] = per_site.get(v["source"], 0) + 1
    sites = ", ".join(f"{s} {n}" for s, n in sorted(per_site.items()))
    out = []
    if first_run:
        out += [f"**Baseline:** {len(pool)} listings ({sites}) — {today}. "
                "From tomorrow you'll get only changes.", ""]
    else:
        out += [f"**{len(pool)}** listings tracked ({sites}) — {today}", ""]
    if failures:
        out += ["⚠️ **Not checked today:** " + "; ".join(failures), ""]

    if first_run:
        for name, items in grouped(list(pool.values()), names):
            out.append(f"### {name} ({len(items)})")
            out += [row(v, pool) for v in items]
            out.append("")
        return "\n".join(out)

    if added:
        out.append(f"## 🆕 New ({len(added)})")
        for name, items in grouped(added, names):
            out.append(f"**{name}**")
            out += [row(v, pool) for v in items]
            out.append("")
    if changed:
        out.append(f"## 💶 Price changes ({len(changed)})")
        for name, items in grouped(changed, names, key=lambda pc: pc[1],
                                   sort=lambda v: v.get("price") or 0):
            out.append(f"**{name}**")
            for prev, cur in items:
                d = cur["price"] - prev["price"]
                out.append(
                    f"- {'🔻' if d < 0 else '🔺'} `{cur['source']}` [{cur['title']}]({cur['url']}): "
                    f"{eur(prev['price'])} → **{eur(cur['price'])}** ({'+' if d > 0 else '−'}{eur(abs(d))}) · "
                    f"{specs(cur)}"
                )
            out.append("")
    if removed:
        out.append(f"## ❌ Removed / no longer matching ({len(removed)})")
        for name, items in grouped(removed, names):
            out.append(f"**{name}**")
            for v in items:
                days = ""
                try:
                    days = f" · listed ~{(datetime.fromisoformat(today) - datetime.fromisoformat(v['first_seen'])).days} days"
                except (KeyError, ValueError, TypeError):
                    pass
                out.append(f"- `{v['source']}` ~~{v['title']}~~ — last {eur(v.get('price'))} · "
                           f"{DRIVE_LABEL.get(v.get('drive'), 'veoskeem ?')}{days} ([link]({v['url']}))")
            out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
def main() -> int:
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    searches = [s for s in cfg["searches"] if s.get("enabled", True)]
    sites = [x for x in cfg.get("sites", list(SOURCES)) if x in SOURCES]
    names = [s["name"] for s in searches]
    today = datetime.now(timezone.utc).date().isoformat()

    session = requests.Session()
    current: dict[str, dict] = {}
    site_failed: dict[str, str] = {}

    for site in sites:
        cache: dict = {}
        for s in searches:
            if site in site_failed:
                break
            try:
                got = SOURCES[site](session, s, cache)
            except (ScrapeError, requests.RequestException, ValueError, KeyError, TypeError) as e:
                print(f"[{site}] {s['name']}: ERROR {e}", file=sys.stderr)
                traceback.print_exc()
                site_failed[site] = str(e)[:160]
                continue
            print(f"[{site}] {s['name']}: {len(got)} matching")
            for k, v in got.items():
                if k in current:
                    current[k].setdefault("searches", [])
                    if s["name"] not in current[k]["searches"]:
                        current[k]["searches"].append(s["name"])
                else:
                    v["searches"] = [s["name"]]
                    current[k] = v
        if site in site_failed:                     # partial results from a broken site are dropped
            current = {k: v for k, v in current.items() if v["source"] != site}

    ok_sites = set(sites) - set(site_failed)
    if not ok_sites:
        print("ERROR: every site failed", file=sys.stderr)
        return 1
    failures = [f"{s} ({e})" for s, e in site_failed.items()]

    first_run = not STATE_FILE.exists()
    old = {} if first_run else json.loads(STATE_FILE.read_text(encoding="utf-8"))

    # A site that suddenly returns far fewer cars is more likely broken than sold out.
    for site in list(ok_sites):
        before = sum(1 for v in old.values() if v.get("source") == site)
        now = sum(1 for v in current.values() if v["source"] == site)
        if before >= 6 and now < 0.5 * before:
            failures.append(f"{site} (only {now} listings vs {before} before — site change?)")
            ok_sites.discard(site)
            current = {k: v for k, v in current.items() if v["source"] != site}

    added, changed, removed, merged = diff(old, current, today, ok_sites)

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(merged, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")

    if first_run or added or changed or removed or failures:
        REPORT_FILE.write_text(build_report(added, changed, removed, merged, failures, names, today, first_run),
                               encoding="utf-8")
    else:
        print("No changes.")
        REPORT_FILE.unlink(missing_ok=True)

    per = {}
    for v in merged.values():
        per[v["source"]] = per.get(v["source"], 0) + 1
    print(f"new={len(added)} changed={len(changed)} removed={len(removed)} total={len(merged)} per_site={per} failed={failures}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
