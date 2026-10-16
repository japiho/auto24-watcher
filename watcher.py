#!/usr/bin/env python3
"""
auto24.ee search watcher.

Fetches one or more auto24 search result URLs, compares the listings with the
previous snapshot (state/listings.json) and writes a Markdown report of:
  - new listings
  - price changes
  - removed listings (probably sold)

Exit codes:
  0  ran fine (report.md written only when something changed)
  1  scraping failed / looked broken (no listings parsed, count mismatch, ...)
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "searches.json"
STATE_FILE = ROOT / "state" / "listings.json"
REPORT_FILE = ROOT / "report.md"
DEBUG_DIR = ROOT / "debug"

BASE = "https://www.auto24.ee"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
    ),
    "Accept-Language": "et-EE,et;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

LISTING_HREF = re.compile(r"/soidukid/(\d+)")
YEAR_RE = re.compile(r"\b(19[89]\d|20[0-4]\d)\b")
KM_RE = re.compile(r"(\d[\d\s ]*)\s*km\b")
PRICE_RE = re.compile(r"(\d[\d\s ]*)\s*€")
TOTAL_RE = re.compile(r"Kokku:\s*(?:<[^>]+>)?\s*(\d+)")
FUELS = [
    "Pistikhübriid (bensiin / elekter)", "Pistikhübriid (diisel / elekter)",
    "Hübriid (bensiin / elekter)", "Hübriid (diisel / elekter)", "Hübriid",
    "Bensiin + gaas", "Bensiin", "Diisel", "Elekter",
]


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
def with_page(url: str, page: int) -> str:
    """Return url with ak=<offset> for pagination (auto24 uses ak = row offset)."""
    parts = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "ak"]
    per_page = int(dict(q).get("af", 50) or 50)
    q.append(("ak", str(page * per_page)))
    return urlunsplit(parts._replace(query=urlencode(q)))


def fetch(session: requests.Session, url: str) -> str:
    for attempt in range(3):
        try:
            r = session.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            if attempt == 2:
                raise
            print(f"  fetch failed ({e}); retrying…", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("unreachable")


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def clean_int(s: str) -> int | None:
    digits = re.sub(r"\D", "", s or "")
    return int(digits) if digits else None


def find_row(a_tag):
    """Walk up from a listing link to the element that holds the whole row."""
    node = a_tag
    for _ in range(8):
        node = node.parent
        if node is None:
            return None
        cls = " ".join(node.get("class", []))
        if "result-row" in cls or node.get("data-hsh"):
            return node
        # fallback: a container that has a price and km inside
        txt = node.get_text(" ", strip=True)
        if "€" in txt and " km" in txt and len(LISTING_HREF.findall(str(node))) <= 6:
            return node
    return None


def parse_listings(html: str) -> tuple[dict[str, dict], int | None]:
    soup = BeautifulSoup(html, "html.parser")
    total = None
    m = TOTAL_RE.search(html)
    if m:
        total = int(m.group(1))

    listings: dict[str, dict] = {}
    # Restrict to the main results list when we can find it, so the
    # "related ads" carousel in the middle of the page is ignored.
    container = soup.select_one("#usedVehiclesSearchResult") or soup.select_one(
        ".search-result"
    ) or soup

    for a in container.find_all("a", href=LISTING_HREF):
        m = LISTING_HREF.search(a["href"])
        lid = m.group(1)
        if lid in listings:
            continue
        row = find_row(a)
        if row is None:
            continue
        text = row.get_text(" ", strip=True)
        # carousel/"similar" cards say "EUR" not "€" and have no km — skip
        if " km" not in text:
            continue

        # Title: prefer the explicit title element, else the link text
        title_el = row.select_one(".title a, .title, a.main")
        title = (title_el.get_text(" ", strip=True) if title_el else a.get_text(" ", strip=True))
        title = re.sub(r"\s+", " ", title).strip() or f"Listing {lid}"

        price_el = row.select_one(".price, .pv")
        price_txt = price_el.get_text(" ", strip=True) if price_el else text
        pm = PRICE_RE.search(price_txt)
        price = clean_int(pm.group(1)) if pm else None

        km_el = row.select_one(".mileage")
        kmm = KM_RE.search(km_el.get_text(" ", strip=True) if km_el else text)
        mileage = clean_int(kmm.group(1)) if kmm else None

        year_el = row.select_one(".year")
        ym = YEAR_RE.search(year_el.get_text() if year_el else text)
        year = int(ym.group(1)) if ym else None

        fuel = next((f for f in FUELS if f.lower() in text.lower()), None)

        listings[lid] = {
            "id": lid,
            "url": f"{BASE}/soidukid/{lid}",
            "title": title,
            "year": year,
            "price": price,
            "mileage": mileage,
            "fuel": fuel,
        }
    return listings, total


def scrape_search(session, name: str, url: str) -> dict[str, dict]:
    print(f"[{name}] {url}")
    found: dict[str, dict] = {}
    total = None
    for page in range(20):  # hard stop
        html = fetch(session, with_page(url, page) if page else url)
        items, page_total = parse_listings(html)
        if page == 0:
            total = page_total
            DEBUG_DIR.mkdir(exist_ok=True)
            (DEBUG_DIR / f"{slug(name)}.html").write_text(html, encoding="utf-8")
        new = {k: v for k, v in items.items() if k not in found}
        found.update(new)
        if not new or total is None or len(found) >= total:
            break
        time.sleep(2)
    print(f"  parsed {len(found)} listings (site says {total})")
    if total is not None and total > 0 and len(found) == 0:
        raise RuntimeError(f"[{name}] site reports {total} results but 0 were parsed — markup changed?")
    if total is not None and len(found) < total:
        print(f"  WARNING: parsed {len(found)} < site total {total}", file=sys.stderr)
    for v in found.values():
        v["search"] = name
    return found


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "search"


# --------------------------------------------------------------------------- #
# Diff + report
# --------------------------------------------------------------------------- #
def eur(n: int | None) -> str:
    return "?" if n is None else f"{n:,}".replace(",", " ") + " €"


def km(n: int | None) -> str:
    return "?" if n is None else f"{n:,}".replace(",", " ") + " km"


def line(v: dict) -> str:
    bits = [str(v.get("year") or "?"), km(v.get("mileage"))]
    if v.get("fuel"):
        bits.append(v["fuel"])
    return f"[{v['title']}]({v['url']}) — **{eur(v.get('price'))}** · " + " · ".join(bits)


def diff(old: dict, new: dict, today: str):
    added, changed, removed = [], [], []
    merged: dict[str, dict] = {}

    for lid, cur in new.items():
        prev = old.get(lid)
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
        merged[lid] = cur

    for lid, prev in old.items():
        if lid not in new:
            removed.append(prev)

    return added, changed, removed, merged


def build_report(added, changed, removed, total_now: int, today: str) -> str:
    out = [f"**{total_now}** listings tracked today ({today}).", ""]
    if added:
        out.append(f"### 🆕 New listings ({len(added)})")
        out += [f"- {line(v)}" for v in sorted(added, key=lambda x: x.get("price") or 0)]
        out.append("")
    if changed:
        out.append(f"### 💶 Price changes ({len(changed)})")
        for prev, cur in sorted(changed, key=lambda pc: (pc[1]["price"] - pc[0]["price"])):
            d = cur["price"] - prev["price"]
            arrow = "🔻" if d < 0 else "🔺"
            out.append(
                f"- {arrow} [{cur['title']}]({cur['url']}): {eur(prev['price'])} → "
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
            out.append(f"- ~~{v['title']}~~ — last {eur(v.get('price'))}{days} ([link]({v['url']}))")
        out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
def main() -> int:
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    searches = cfg["searches"]
    today = datetime.now(timezone.utc).date().isoformat()

    session = requests.Session()
    current: dict[str, dict] = {}
    try:
        for s in searches:
            current.update(scrape_search(session, s["name"], s["url"]))
            time.sleep(2)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    first_run = not STATE_FILE.exists()
    old = {} if first_run else json.loads(STATE_FILE.read_text(encoding="utf-8"))

    # Guard against a half-broken scrape wiping state: if we suddenly see far
    # fewer listings than before, don't treat them all as sold.
    if old and len(current) < 0.5 * len(old):
        print(
            f"ERROR: only {len(current)} listings vs {len(old)} yesterday — "
            "refusing to update state (site change or block?)",
            file=sys.stderr,
        )
        return 1

    added, changed, removed, merged = diff(old, current, today)

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(merged, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")

    if first_run:
        report = (
            f"First run — baseline saved with **{len(merged)}** listings. "
            "From tomorrow you'll get new listings and price changes here.\n\n"
            + "\n".join(f"- {line(v)}" for v in sorted(merged.values(), key=lambda x: x.get("price") or 0))
        )
        REPORT_FILE.write_text(report, encoding="utf-8")
    elif added or changed or removed:
        REPORT_FILE.write_text(build_report(added, changed, removed, len(merged), today), encoding="utf-8")
    else:
        print("No changes.")
        REPORT_FILE.unlink(missing_ok=True)

    print(f"new={len(added)} changed={len(changed)} removed={len(removed)} total={len(merged)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
