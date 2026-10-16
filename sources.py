"""
Site scrapers. Every source takes a *search spec* (see searches.json) and returns
{key: listing} with normalised fields:

  key, source, id, url, title, make, model, year, price, mileage, power_kw,
  fuel, body, drive, seller

body  : lower-case Estonian ("universaal", "mahtuniversaal", "sedaan", ...)
drive : "esivedu" | "tagavedu" | "nelikvedu" | None (unknown)

Sites and what their robots.txt allows (checked Oct 2026):
  autoportaal.ee  everything                -> filtered search pages (HTML)
  autodiiler.ee   public pages, NOT the API -> listings per make, filtered locally
  veego.ee        api.veego.ee allows all   -> POST /api/v2/search (the site's own search API)
  longo.ee        everything                -> filtered catalog pages (Nuxt payload)
  carprof.ee      no robots.txt             -> catalog page + detail pages of candidates
  auto24.ee       Cloudflare blocks scripts -> not scraped
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "et-EE,et;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
DEBUG_DIR = Path(__file__).resolve().parent / "debug"
DELAY = 1.5        # seconds between requests to the same site
MAX_PAGES = 40

DRIVES = ("esivedu", "tagavedu", "nelikvedu")

# Site-specific ids for the makes used in searches.json -----------------------
AUTOPORTAAL_MAKE = {"Mercedes-Benz": 59, "Mercedes-AMG": 103, "BMW": 15, "Skoda": 80,
                    "Subaru": 83, "Volkswagen": 91}
AUTOPORTAAL_BODY = {"universaal": 3, "mahtuniversaal": 7, "sedaan": 1, "luukpära": 2}
AUTOPORTAAL_DRIVE = {"esivedu": 1, "tagavedu": 2, "nelikvedu": 3}

AUTODIILER_BRAND = {"Mercedes-Benz": 18, "BMW": 3, "Skoda": 29, "Subaru": 30, "Volkswagen": 34}

VEEGO_BODY = {"sedaan": 1, "luukpära": 2, "universaal": 3, "mahtuniversaal": 4}
VEEGO_DRIVE = {"esivedu": 1, "tagavedu": 2, "nelikvedu": 3}

LONGO_MAKE = {"Mercedes-Benz": "MERCEDES-BENZ", "BMW": "BMW", "Skoda": "SKODA",
              "Subaru": "SUBARU", "Volkswagen": "VOLKSWAGEN"}
LONGO_BODY = {"universaal": "Wagon", "mahtuniversaal": "Minivan", "sedaan": "Sedan",
              "luukpära": "Hatchback", "maastur": "Suv", "kupee": "Coupe"}
LONGO_BODY_BACK = {v: k for k, v in LONGO_BODY.items()}
LONGO_DRIVE = {"esivedu": "FWD", "tagavedu": "RWD", "nelikvedu": "AWD"}


class ScrapeError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _req(session, method, url, tag, **kw):
    for attempt in range(3):
        try:
            r = session.request(method, url, headers=kw.pop("headers", HEADERS), timeout=30, **kw)
            if r.status_code >= 400:
                DEBUG_DIR.mkdir(exist_ok=True)
                (DEBUG_DIR / f"{tag}_error_{r.status_code}.html").write_text(r.text, encoding="utf-8")
                if r.headers.get("cf-mitigated") == "challenge":
                    raise ScrapeError(f"{tag}: blocked by Cloudflare security check")
            r.raise_for_status()
            time.sleep(DELAY)
            return r
        except ScrapeError:
            raise
        except requests.RequestException as e:
            if attempt == 2:
                raise ScrapeError(f"{tag}: {e}") from e
            print(f"  {tag}: {e}; retrying…", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    raise AssertionError


def get(session, url, tag) -> str:
    return _req(session, "GET", url, tag).text


def to_int(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return int(s)
    if re.fullmatch(r"\s*\d+\.\d+\s*", str(s)):        # "27900.00"
        return int(float(s))
    digits = re.sub(r"\D", "", str(s))
    return int(digits) if digits else None


def norm_drive(s):
    s = (s or "").lower()
    for d in DRIVES:
        if d in s:
            return d
    if any(w in s for w in ("awd", "4x4", "4wd")):
        return "nelikvedu"
    if "fwd" in s:
        return "esivedu"
    if "rwd" in s:
        return "tagavedu"
    return None


def norm_body(s):
    s = (s or "").strip().lower()
    return s or None


def strip_make(title: str, makes) -> str:
    for m in sorted(makes, key=len, reverse=True):
        if title.lower().startswith(m.lower()):
            return title[len(m):].strip(" -")
    return title


def save_debug(tag, text):
    DEBUG_DIR.mkdir(exist_ok=True)
    (DEBUG_DIR / f"{tag}.html").write_text(text, encoding="utf-8")


_NUXT_WRAP = {"Reactive", "ShallowReactive", "Ref", "ShallowRef", "EmptyRef", "EmptyShallowRef", "NuxtError"}


def nuxt_payload(html, tag):
    m = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise ScrapeError(f"{tag}: no __NUXT_DATA__ on page — site changed?")
    return json.loads(m.group(1))


def nuxt_resolve(d, i, depth=0):
    if not isinstance(i, int) or depth > 50:
        return i
    v = d[i]
    if isinstance(v, list):
        if v and isinstance(v[0], str) and v[0] in _NUXT_WRAP:
            return nuxt_resolve(d, v[1], depth + 1) if len(v) > 1 else None
        if v and isinstance(v[0], str) and v[0] in ("Set", "Map", "Date", "BigInt", "undefined"):
            return None
        return [nuxt_resolve(d, x, depth + 1) for x in v]
    if isinstance(v, dict):
        return {k: nuxt_resolve(d, x, depth + 1) for k, x in v.items()}
    return v


def nuxt_objects(d, required: set):
    return [nuxt_resolve(d, i) for i, x in enumerate(d) if isinstance(x, dict) and required <= set(x)]


def detail_attrs(html: str) -> dict:
    """Pull kW / body / drive from a car's own page (works on veego, longo, carprof)."""
    text = BeautifulSoup(html, "html.parser").get_text(" | ", strip=True)
    out = {}
    m = re.search(r"(?:Vedav sild|Veoskeem)\s*\|\s*([A-Za-zÕÄÖÜõäöü ]+?)\s*\|", text)
    if m:
        out["drive"] = norm_drive(m.group(1))
    m = re.search(r"Keretüüp\s*\|\s*([A-Za-zÕÄÖÜõäöü ]+?)\s*\|", text)
    if m:
        out["body"] = norm_body(m.group(1))
    m = re.search(r"(?:Võimsus|Mootori võimsus)\s*\|\s*(\d+)\s*kW", text) or re.search(r"\b(\d{2,3})\s*kW\b", text)
    if m:
        out["power_kw"] = int(m.group(1))
    return out


# --------------------------------------------------------------------------- #
# Matching a listing against a search spec
# --------------------------------------------------------------------------- #
def matches(v: dict, s: dict, *, strict_unknown=False) -> bool:
    """strict_unknown=False: unknown body/drive/kW passes (shown as '?')."""
    rx = s.get("model_regex")
    if rx and not re.search(rx, v.get("model") or "", re.I):
        return False

    def ok(val, lo=None, hi=None):
        if val is None:
            return not strict_unknown and (lo is None or True)
        return (lo is None or val >= lo) and (hi is None or val <= hi)

    if v.get("year") is None or not ok(v["year"], s.get("year_min"), s.get("year_max")):
        return False
    if v.get("price") is None or not ok(v["price"], s.get("price_min"), s.get("price_max")):
        return False
    if s.get("mileage_max") and (v.get("mileage") is None or v["mileage"] > s["mileage_max"]):
        return False
    if s.get("power_min") and v.get("power_kw") is not None and v["power_kw"] < s["power_min"]:
        return False
    if s.get("power_min") and v.get("power_kw") is None and strict_unknown:
        return False
    bodies = s.get("bodies")
    if bodies and v.get("body") is not None and v["body"] not in bodies:
        return False
    if bodies and v.get("body") is None and strict_unknown:
        return False
    drive = s.get("drive")
    if drive and v.get("drive") is not None and v["drive"] != drive:
        return False
    if drive and v.get("drive") is None and strict_unknown:
        return False
    return True


def all_makes(s):
    return [s["make"]] + s.get("make_aliases", [])


# --------------------------------------------------------------------------- #
# autoportaal.ee — HTML search page with filters in the URL
# --------------------------------------------------------------------------- #
def autoportaal(session, s, cache) -> dict:
    tag = "autoportaal"
    out = {}
    for make in all_makes(s):
        if make not in AUTOPORTAAL_MAKE:
            continue
        q = [("ok", 1), ("make_id", AUTOPORTAAL_MAKE[make]), ("sort", "id_desc")]
        for k, p in (("year_min", "year_min"), ("price_max", "price_max"),
                     ("power_min", "power_min"), ("mileage_max", "mileage_max")):
            if s.get(k):
                q.append((p, s[k]))
        for b in s.get("bodies", []):
            if b in AUTOPORTAAL_BODY:
                q.append(("body_type[]", AUTOPORTAAL_BODY[b]))
        # drive is NOT filtered server-side: cars without a drive value would be dropped
        base = "https://autoportaal.ee/et/kuulutused?" + urlencode(q)

        seen_any = {}
        for page in range(1, MAX_PAGES):
            url = base if page == 1 else f"{base}&page={page}"
            html = cache.get(url) or get(session, url, tag)
            cache[url] = html
            if page == 1:
                save_debug(f"{tag}_{make}", html)
            soup = BeautifulSoup(html, "html.parser")
            new = 0
            for card in soup.select("div.advertisement[id^=a]"):
                lid = card["id"][1:]
                if not lid.isdigit() or lid in seen_any:
                    continue
                seen_any[lid] = 1
                new += 1
                desk = ".additionalData:not(.additionalDataMobile)"
                li = lambda c: card.select_one(f"{desk} li.{c}")  # noqa: E731
                fuels = [x.get_text(strip=True) for x in card.select(f"{desk} li.fuel")]
                power = next((int(m.group(1)) for f in fuels if (m := re.search(r"(\d+)\s*kW", f))), None)
                title = (card.select_one("h2").get_text(" ", strip=True) if card.select_one("h2") else f"Listing {lid}")
                out[f"autoportaal:{lid}"] = {
                    "source": "autoportaal", "id": lid,
                    "url": f"https://autoportaal.ee/et/kasutatud-autod/{lid}",
                    "title": title, "make": make, "model": strip_make(title, all_makes(s)),
                    "year": to_int(li("year").get_text()) if li("year") else None,
                    "price": to_int(card.select_one(".finalPrice").get_text()) if card.select_one(".finalPrice") else None,
                    "mileage": to_int(li("mileage").get_text()) if li("mileage") else None,
                    "power_kw": power,
                    "fuel": next((f for f in fuels if f and "kW" not in f and f != "-"), None),
                    "body": norm_body(li("body_type").get_text()) if li("body_type") else None,
                    "drive": norm_drive(li("drive").get_text()) if li("drive") else None,
                    "seller": card.select_one(".seller").get_text(" ", strip=True) if card.select_one(".seller") else None,
                }
            if not new:
                break
    return {k: v for k, v in out.items() if matches(v, s)}


# --------------------------------------------------------------------------- #
# autodiiler.ee — public pages per make (Nuxt payload), filtered locally
# --------------------------------------------------------------------------- #
def autodiiler(session, s, cache) -> dict:
    tag = "autodiiler"
    out = {}
    for make in all_makes(s):
        if make not in AUTODIILER_BRAND:
            continue
        ck = f"autodiiler:make:{make}"
        if ck not in cache:
            listings = {}
            base = f"https://autodiiler.ee/et/vehicles?s=default&vt=1&ba={AUTODIILER_BRAND[make]}"
            for page in range(1, MAX_PAGES * 3):
                html = get(session, f"{base}&page={page}", tag)
                if page == 1:
                    save_debug(f"{tag}_{make}", html)
                new = 0
                for v in nuxt_objects(nuxt_payload(html, tag), {"price", "mileage", "brandModel", "id"}):
                    lid = str(v["id"])
                    if f"autodiiler:{lid}" in listings:
                        continue
                    new += 1
                    desc = (v.get("seo") or {}).get("description") or ""
                    ym = re.match(r"\s*(\d{4})\s*\|", desc)
                    listings[f"autodiiler:{lid}"] = {
                        "source": "autodiiler", "id": lid,
                        "url": f"https://autodiiler.ee/et/vehicles/{lid}",
                        "title": v.get("name") or f"Listing {lid}", "make": make,
                        "model": (v.get("brandModel") or {}).get("name") or "",
                        "year": int(ym.group(1)) if ym else (int(v["registered_date"][:4]) if v.get("registered_date") else None),
                        "price": to_int(v.get("price")), "mileage": to_int(v.get("mileage")),
                        "power_kw": to_int(v.get("engine_power")),
                        "fuel": (v.get("fuel_type") or {}).get("label"),
                        "body": norm_body((v.get("vehicleBodyType") or {}).get("name")),
                        "drive": norm_drive((v.get("drive_type") or {}).get("label")),
                        "seller": v.get("seller_name"),
                        "reserved": bool(v.get("is_reserved")),
                    }
                if not new:
                    break
            print(f"  autodiiler: {len(listings)} {make} listings")
            cache[ck] = listings
        out.update({k: dict(v) for k, v in cache[ck].items() if matches(v, s)})
    return out


# --------------------------------------------------------------------------- #
# veego.ee — the site's own search API (api.veego.ee robots.txt: Allow /)
# One query per drive type, so every result's drive is known.
# --------------------------------------------------------------------------- #
def veego(session, s, cache) -> dict:
    tag = "veego"
    hdr = {**HEADERS, "Accept": "application/json", "Origin": "https://veego.ee", "Referer": "https://veego.ee/"}

    def query(term, drive_id=None):
        body = {"order_by": "Standard", "is_new": 0, "term": term}
        if drive_id:
            body["drivetrain_ids"] = [drive_id]
        for k, p in (("year_min", "year_from"), ("price_max", "price_to"),
                     ("power_min", "kw_from"), ("mileage_max", "mileage_to")):
            if s.get(k):
                body[p] = s[k]
        if s.get("bodies"):
            body["body_type_ids"] = [VEEGO_BODY[b] for b in s["bodies"] if b in VEEGO_BODY]
        results = []
        for page in range(1, MAX_PAGES):
            body["page"] = page
            data = _req(session, "POST", "https://api.veego.ee/api/v2/search", tag, json=body, headers=hdr).json()
            results += data.get("results") or []
            if page >= (data.get("pages") or 0):
                break
        print(f"    veego term={term!r} drive_id={drive_id}: {len(results)}")
        return results

    out, drive_of = {}, {}
    for term in s.get("veego_terms") or [s["make"]]:
        everything = query(term)                       # all cars, including ones with no drive set
        if not everything:
            continue
        for d in DRIVES:                               # label each car's drive
            for v in query(term, VEEGO_DRIVE[d]):
                drive_of[v["id"]] = d
        for v in everything:
            title = v.get("title") or ""
            kw = re.search(r"(\d+)\s*kW", title)
            out[f"veego:{v['id']}"] = {
                "source": "veego", "id": str(v["id"]),
                "url": f"https://veego.ee/kasutatud-autod/{v['id']}",
                "title": title, "make": s["make"], "model": strip_make(title, all_makes(s)),
                "year": to_int(v.get("year")), "price": to_int(v.get("active_price") or v.get("price")),
                "mileage": to_int(v.get("odometer")), "power_kw": int(kw.group(1)) if kw else None,
                "fuel": {"fuel-abbr-diesel": "Diisel", "fuel-abbr-petrol": "Bensiin"}.get(v.get("fuel")),
                "body": None,                          # filtered server-side (body_type_ids)
                "drive": drive_of.get(v["id"]),
                "seller": (v.get("user") or {}).get("name"),
            }
    result = {k: v for k, v in out.items() if matches(v, s)}
    # cars with no drive in the search data: look at their own page (cached for the run)
    details = cache.setdefault("veego:details", {})
    for k, v in result.items():
        if v["drive"] is None and s.get("drive"):
            if v["id"] not in details and len(details) < 25:
                details[v["id"]] = detail_attrs(get(session, v["url"], tag))
            v["drive"] = (details.get(v["id"]) or {}).get("drive")
    return {k: v for k, v in result.items() if matches(v, s)}


# --------------------------------------------------------------------------- #
# longo.ee — catalog pages per make with filters in the URL (Nuxt payload).
# One query per body type × drive type, so both are known for every car.
# --------------------------------------------------------------------------- #
def longo(session, s, cache) -> dict:
    tag = "longo"
    out = {}
    make = LONGO_MAKE.get(s["make"])
    if not make:
        return out
    drives = [s["drive"]] if s.get("drive") else list(DRIVES)
    bodies = [b for b in s.get("bodies", []) if b in LONGO_BODY] or [None]
    for body in bodies:
        for drive in drives:
            q = [("driveTypes", LONGO_DRIVE[drive])]
            if body:
                q.append(("bodyTypes", LONGO_BODY[body]))
            for k, p in (("year_min", "yearFrom"), ("price_max", "priceTo"),
                         ("power_min", "powerKwFrom"), ("mileage_max", "mileageTo")):
                if s.get(k):
                    q.append((p, s[k]))
            base = f"https://longo.ee/kataloog/mark/{make}?" + urlencode(q)
            seen = set()
            for page in range(1, MAX_PAGES):
                url = base if page == 1 else f"{base}&currentPage={page}"
                html = get(session, url, tag)
                cars = nuxt_objects(nuxt_payload(html, tag), {"stockNumber", "make", "model", "price"})
                new = [c for c in cars if c["id"] not in seen]
                for c in new:
                    seen.add(c["id"])
                    model = " ".join(x for x in (c.get("model"), c.get("series"), c.get("variant")) if x)
                    title = f"{s['make']} {c.get('model', '')} {c.get('variant') or ''}".strip()
                    slug = f"{c['make']}-{c['model']}-{c['year']}".replace(" ", "-")
                    out[f"longo:{c['stockNumber']}"] = {
                        "source": "longo", "id": c["stockNumber"],
                        "url": f"https://longo.ee/auto/{slug}/{c['stockNumber']}",
                        "title": title, "make": s["make"], "model": model,
                        "year": to_int(c.get("year")), "price": to_int(c.get("price")),
                        "mileage": to_int(c.get("mileage")),
                        "power_kw": None,             # filtered server-side (powerKwFrom)
                        "fuel": c.get("fuelTypeLabel"),
                        "body": LONGO_BODY_BACK.get(c.get("bodyType"), norm_body(c.get("bodyType"))),
                        "drive": drive, "seller": "Longo",
                    }
                if not new or len(cars) < 24:
                    break
    return {k: v for k, v in out.items() if matches(v, s)}


# --------------------------------------------------------------------------- #
# carprof.ee — one dealer, ~30 cars on one page; candidates get a detail fetch
# --------------------------------------------------------------------------- #
def carprof(session, s, cache) -> dict:
    tag = "carprof"
    if "carprof:catalog" not in cache:
        html = get(session, "https://carprof.ee/kataloog", tag)
        save_debug(tag, html)
        soup = BeautifulSoup(html, "html.parser")
        cars = {}
        for a in soup.select("a.car-card[href*='/kataloog/auto/']"):
            m = re.search(r"/(\d+)$", a["href"])
            if not m:
                continue
            lid = m.group(1)
            title = a.select_one(".car-card__title").get_text(" ", strip=True)
            sub = a.select_one(".car-card__subtitle")
            tags = [t.get_text(strip=True) for t in a.select(".tags-list__value")]
            price_el = a.select_one(".car-card__bymonth")
            cars[lid] = {
                "source": "carprof", "id": lid, "url": "https://carprof.ee" + a["href"],
                "title": (title + (" " + sub.get_text(" ", strip=True) if sub and sub.get_text(strip=True) else "")),
                "card_title": title,
                "year": to_int(a.get("data-year")),
                "price": to_int(price_el.get_text()) if price_el else None,
                "mileage": next((to_int(t) for t in tags if t.endswith("km")), None),
                "fuel": next((t for t in tags if t in ("Diisel", "Bensiin", "Hübriid", "Elekter", "Pistikhübriid")), None),
                "power_kw": None, "body": None, "drive": None, "seller": "CarProf",
            }
        cache["carprof:catalog"] = cars
        cache["carprof:details"] = {}
        print(f"    carprof catalog: {len(cars)} cars: " + ", ".join(sorted({c['card_title'] for c in cars.values()}))[:600])
    out = {}
    for lid, c in cache["carprof:catalog"].items():
        v = dict(c)
        makes = all_makes(s)
        title_l = v["card_title"].lower().replace("ë", "e")
        if not any(title_l.startswith(m.lower()) for m in makes):
            continue
        v["make"] = s["make"]
        v["model"] = strip_make(v["card_title"], makes)
        if not matches(v, s):                 # cheap pre-filter on model/year/price/km
            continue
        if lid not in cache["carprof:details"]:
            cache["carprof:details"][lid] = detail_attrs(get(session, v["url"], tag))
        v.update({k: x for k, x in cache["carprof:details"][lid].items() if x is not None})
        v.pop("card_title", None)
        if matches(v, s):                     # detail pages don't always show kW
            out[f"carprof:{lid}"] = v
    return out


# --------------------------------------------------------------------------- #
SOURCES = {
    "autoportaal": autoportaal,
    "autodiiler": autodiiler,
    "veego": veego,
    "longo": longo,
    "carprof": carprof,
}
