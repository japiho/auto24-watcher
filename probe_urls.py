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
        if isinstance(url, dict):
            r = requests.post(url["url"], json=url["json"], headers={**H, "Accept": "application/json",
                              "Origin": "https://veego.ee", "Referer": "https://veego.ee/"}, timeout=30)
        else:
            r = requests.get(url, headers=H, timeout=30)
        res[name] = {"url": url, "status": r.status_code, "final": r.url, "server": r.headers.get("server"),
                     "cf_mitigated": r.headers.get("cf-mitigated"), "ctype": r.headers.get("content-type"),
                     "len": len(r.text)}
        open(f"{out}/{name}.txt", "w", encoding="utf-8").write(r.text)
    except Exception as e:
        res[name] = {"url": url, "error": str(e)}
    time.sleep(1)

# For pages named *_js: fetch their JS bundles and save snippets around API paths.
import re
from urllib.parse import urljoin
for name, url in URLS.items():
    if not name.endswith("_js") or isinstance(url, dict):
        continue
    html = open(f"{out}/{name}.txt", encoding="utf-8").read()
    srcs = sorted(set(re.findall(r'(?:src|href)="([^"]+\.js)"', html)))
    snippets = []
    for s in srcs:
        u = urljoin(url, s)
        try:
            js = requests.get(u, headers=H, timeout=30).text
        except Exception as e:
            snippets.append(f"!! {u}: {e}")
            continue
        for m in re.finditer(r'(vehicles?/search|/search|vehicles\?|api/vehicles|"vehicles")', js):
            snippets.append(f"--- {s} @{m.start()}\n" + js[max(0, m.start() - 300): m.start() + 300])
    open(f"{out}/{name}_snippets.txt", "w", encoding="utf-8").write(
        f"{len(srcs)} scripts\n" + "\n".join(snippets[:400]))
json.dump(res, open(f"{out}/summary.json", "w"), indent=1)
print(json.dumps(res, indent=1))
