"""Temporary helper: fetch a list of URLs and save status + body (for site discovery)."""
import json
import sys
import time

import requests

H = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
    "Accept-Language": "et-EE,et;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
}
URLS = json.load(open("probe_urls.json"))
out = sys.argv[1]
res = {}
for name, url in URLS.items():
    try:
        r = requests.get(url, headers=H, timeout=30)
        res[name] = {"url": url, "status": r.status_code, "final": r.url, "server": r.headers.get("server"),
                     "cf_mitigated": r.headers.get("cf-mitigated"), "ctype": r.headers.get("content-type"),
                     "len": len(r.text)}
        open(f"{out}/{name}.txt", "w", encoding="utf-8").write(r.text)
    except Exception as e:
        res[name] = {"url": url, "error": str(e)}
    time.sleep(1)
json.dump(res, open(f"{out}/summary.json", "w"), indent=1)
print(json.dumps(res, indent=1))
