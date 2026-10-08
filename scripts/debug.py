import json, requests
from urllib.parse import quote

URL = "https://atlas.emory.edu/api/"
params = {"page": "fose", "route": "search", "keyword": "MATH"}
body = {"other": {"srcdb": "5269"}, "criteria": [{"field": "keyword", "value": "MATH"}]}
raw = json.dumps(body)

base = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://atlas.emory.edu/",
    "Origin": "https://atlas.emory.edu",
    "X-Requested-With": "XMLHttpRequest",
}
variants = {
    "A: raw JSON, json header": (raw, {"Content-Type": "application/json"}),
    "B: raw JSON, form header": (raw, {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}),
    "C: url-encoded JSON, form header": (quote(raw), {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}),
    "D: raw JSON, text/plain": (raw, {"Content-Type": "text/plain;charset=UTF-8"}),
}
for name, (data, hdr) in variants.items():
    r = requests.post(URL, params=params, data=data, headers={**base, **hdr}, timeout=30)
    print(f"\n{name}\n  status={r.status_code}  type={r.headers.get('Content-Type')}\n  body[:150]={r.text[:150]!r}")