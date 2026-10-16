"""
Per-site scrapers. Each returns {listing_key: listing_dict} where listing_dict has:
  id, source, url, title, year, price, mileage, power_kw, fuel, body, seller
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
    ),
    "Accept-Language": "et-EE,et;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
DEBUG_DIR = Path(__file__).resolve().parent / "debug"
PAGE_DELAY = 2  # seconds between page requests — be polite
MAX_PAGES = 30


class ScrapeError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
def fetch(session: requests.Session, url: str, tag: str) -> str:
    for attempt in range(3):
        try:
            r = session.get(url, headers=HEADERS, timeout=30)
            if r.status_code >= 400:
                DEBUG_DIR.mkdir(exist_ok=True)
                (DEBUG_DIR / f"{tag}_error_{r.status_code}.html").write_text(r.text, encoding="utf-8")
                (DEBUG_DIR / f"{tag}_error_{r.status_code}_headers.txt").write_text(
                    "\n".join(f"{k}: {v}" for k, v in r.headers.items()), encoding="utf-8"
                )
                if r.headers.get("cf-mitigated") == "challenge":
                    raise ScrapeError(f"{tag}: blocked by Cloudflare security check (HTTP {r.status_code})")
            r.raise_for_status()
            return r.text
        except ScrapeError:
            raise
        except requests.RequestException as e:
            if attempt == 2:
                raise ScrapeError(f"{tag}: {e}") from e
            print(f"  fetch failed ({e}); retrying…", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    raise AssertionError("unreachable")


def set_param(url: str, key: str, value) -> str:
    parts = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != key]
    q.append((key, str(value)))
    return urlunsplit(parts._replace(query=urlencode(q)))


def to_int(s) -> int | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return int(s)
    digits = re.sub(r"\D", "", str(s))
    return int(digits) if digits else None


def save_debug(tag: str, html: str) -> None:
    DEBUG_DIR.mkdir(exist_ok=True)
    (DEBUG_DIR / f"{tag}.html").write_text(html, encoding="utf-8")


def paginate(session, url: str, tag: str, parse_page, page_param="page", first_page=1) -> dict:
    """Fetch page after page until a page adds no new listings."""
    found: dict = {}
    for n in range(first_page, first_page + MAX_PAGES):
        page_url = url if n == first_page else set_param(url, page_param, n)
        html = fetch(session, page_url, tag)
        if n == first_page:
            save_debug(tag, html)
        items = parse_page(html)
        new = {k: v for k, v in items.items() if k not in found}
        found.update(new)
        if not new:
            break
        time.sleep(PAGE_DELAY)
    return found


# --------------------------------------------------------------------------- #
# autoportaal.ee — server-rendered HTML, filters go in the URL
# --------------------------------------------------------------------------- #
def autoportaal(session, search: dict) -> dict:
    tag = "autoportaal"

    def parse(html: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        out = {}
        for card in soup.select("div.advertisement[id^=a]"):
            lid = card["id"][1:]
            if not lid.isdigit():
                continue
            title_el = card.select_one("h2")
            price_el = card.select_one(".finalPrice")
            li = lambda cls: card.select_one(f".additionalData:not(.additionalDataMobile) li.{cls}")  # noqa: E731
            year_el, km_el, body_el = li("year"), li("mileage"), li("body_type")
            fuels = [x.get_text(strip=True) for x in card.select(".additionalData:not(.additionalDataMobile) li.fuel")]
            power = next((to_int(m.group(1)) for f in fuels if (m := re.search(r"(\d+)\s*kW", f))), None)
            fuel = next((f for f in fuels if f and "kW" not in f and f != "-"), None)
            seller_el = card.select_one(".seller")
            out[f"autoportaal:{lid}"] = {
                "id": lid,
                "source": "autoportaal",
                "url": f"https://autoportaal.ee/et/kasutatud-autod/{lid}",
                "title": title_el.get_text(" ", strip=True) if title_el else f"Listing {lid}",
                "year": to_int(year_el.get_text()) if year_el else None,
                "price": to_int(price_el.get_text()) if price_el else None,
                "mileage": to_int(km_el.get_text()) if km_el else None,
                "power_kw": power,
                "fuel": fuel,
                "body": body_el.get_text(strip=True) if body_el else None,
                "seller": seller_el.get_text(" ", strip=True) if seller_el else None,
            }
        return out

    return paginate(session, search["url"], tag, parse)


# --------------------------------------------------------------------------- #
# autodiiler.ee — Nuxt app; listings are embedded in the page as __NUXT_DATA__.
# The site's filter params aren't all known, so we fetch the make's listings
# (vt=1 cars, ba=<brand id>) and filter locally with search["filter"].
# (Its API subdomain disallows crawling in robots.txt, so we only use the
#  public pages, which robots.txt allows.)
# --------------------------------------------------------------------------- #
_WRAPPERS = {"Reactive", "ShallowReactive", "Ref", "ShallowRef", "EmptyRef", "EmptyShallowRef", "NuxtError"}


def _nuxt_payload(html: str) -> list:
    m = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise ScrapeError("autodiiler: no __NUXT_DATA__ on page — site changed?")
    return json.loads(m.group(1))


def _resolve(d: list, i, depth=0):
    if not isinstance(i, int) or depth > 50:
        return i
    v = d[i]
    if isinstance(v, list):
        if v and isinstance(v[0], str) and v[0] in _WRAPPERS:
            return _resolve(d, v[1], depth + 1) if len(v) > 1 else None
        if v and isinstance(v[0], str) and v[0] in ("Set", "Map", "Date", "BigInt", "undefined"):
            return None
        return [_resolve(d, x, depth + 1) for x in v]
    if isinstance(v, dict):
        return {k: _resolve(d, x, depth + 1) for k, x in v.items()}
    return v


def autodiiler(session, search: dict) -> dict:
    tag = "autodiiler"
    flt = search.get("filter", {})

    def parse(html: str) -> dict:
        d = _nuxt_payload(html)
        out = {}
        for i, x in enumerate(d):
            if not (isinstance(x, dict) and {"price", "mileage", "brandModel", "id"} <= set(x)):
                continue
            v = _resolve(d, i)
            lid = str(v["id"])
            desc = ((v.get("seo") or {}).get("description") or "")
            ym = re.match(r"\s*(\d{4})\s*\|", desc)
            year = int(ym.group(1)) if ym else (int(v["registered_date"][:4]) if v.get("registered_date") else None)
            out[f"autodiiler:{lid}"] = {
                "id": lid,
                "source": "autodiiler",
                "url": f"https://autodiiler.ee/et/vehicles/{lid}",
                "title": v.get("name") or f"Listing {lid}",
                "model": (v.get("brandModel") or {}).get("name"),
                "year": year,
                "price": to_int(v.get("price")),
                "mileage": to_int(v.get("mileage")),
                "power_kw": to_int(v.get("engine_power")),
                "fuel": (v.get("fuel_type") or {}).get("label"),
                "body": (v.get("vehicleBodyType") or {}).get("name"),
                "seller": v.get("seller_name"),
                "reserved": bool(v.get("is_reserved")),
            }
        return out

    everything = paginate(session, search["url"], tag, parse)
    print(f"  autodiiler: {len(everything)} listings for the make before local filtering")
    return {k: v for k, v in everything.items() if _matches(v, flt)}


def _matches(v: dict, f: dict) -> bool:
    def ge(val, lim):
        return lim is None or (val is not None and val >= lim)

    def le(val, lim):
        return lim is None or (val is not None and val <= lim)

    model = (v.get("model") or "")
    prefixes = f.get("model_prefixes")
    if prefixes and not any(model.startswith(p) for p in prefixes):
        return False
    bodies = f.get("body_types")
    if bodies and (v.get("body") or "") not in bodies:
        return False
    return (
        ge(v.get("year"), f.get("year_min")) and le(v.get("year"), f.get("year_max"))
        and ge(v.get("price"), f.get("price_min")) and le(v.get("price"), f.get("price_max"))
        and ge(v.get("power_kw"), f.get("power_min")) and le(v.get("mileage"), f.get("mileage_max"))
    )


# --------------------------------------------------------------------------- #
# auto24.ee — currently serves a Cloudflare challenge to scripts, so it's
# disabled in searches.json. Kept in case that changes.
# --------------------------------------------------------------------------- #
def auto24(session, search: dict) -> dict:
    tag = "auto24"
    href = re.compile(r"/soidukid/(\d+)")

    def parse(html: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        out = {}
        for row in soup.select(".result-row"):
            a = row.find("a", href=href)
            if not a:
                continue
            lid = href.search(a["href"]).group(1)
            text = row.get_text(" ", strip=True)
            pm = re.search(r"(\d[\d\s ]*)\s*€", text)
            km = re.search(r"(\d[\d\s ]*)\s*km\b", text)
            ym = re.search(r"\b(19[89]\d|20[0-4]\d)\b", text)
            kw = re.search(r"(\d+)\s*kW", text)
            out[f"auto24:{lid}"] = {
                "id": lid, "source": "auto24", "url": f"https://www.auto24.ee/soidukid/{lid}",
                "title": a.get_text(" ", strip=True) or f"Listing {lid}",
                "year": int(ym.group(1)) if ym else None,
                "price": to_int(pm.group(1)) if pm else None,
                "mileage": to_int(km.group(1)) if km else None,
                "power_kw": to_int(kw.group(1)) if kw else None,
                "fuel": None, "body": None, "seller": None,
            }
        return out

    html = fetch(session, search["url"], tag)  # use af=100 in the URL; one page
    save_debug(tag, html)
    return parse(html)


SOURCES = {"autoportaal": autoportaal, "autodiiler": autodiiler, "auto24": auto24}
