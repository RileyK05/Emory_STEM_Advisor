"""Save the OpenStreetMap survey data for a campus map (one Overpass query).

OpenStreetMap is only the survey: where buildings, paths and lawns are. The map
students see is drawn by `scripts.build_campus_map` in our own style. Emory's
own map is all rights reserved, so it is linked to, never copied.

    python -m scripts.fetch_campus_map [--campus atlanta] [--endpoint URL]

Writes data/maps/raw/<campus>-<UTC time>.json and a .meta.json sidecar with the
endpoint, the query, the time and the SHA-256. Map data © OpenStreetMap
contributors, under the Open Database License.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import time

import httpx

from app.config import load_settings

# The official instance first. The others are public instances listed on the
# OpenStreetMap wiki, for when the official one is down (it answers 502 then).
ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
# south, west, north, east: a box around the campus, generous on purpose;
# `build_campus_map` keeps only what lies inside the campus boundary.
BOXES = {"atlanta": (33.7820, -84.3400, 33.8030, -84.3110)}
QUERY = """[out:json][timeout:120];
(
  nwr["amenity"="university"]({box});
  way["building"]({box});
  relation["building"]({box});
  way["highway"]({box});
  way["landuse"~"^(grass|forest|meadow|recreation_ground)$"]({box});
  way["leisure"~"^(park|garden|pitch|track|stadium|sports_centre|nature_reserve)$"]({box});
  relation["leisure"~"^(park|garden|nature_reserve)$"]({box});
  way["natural"~"^(water|wood|scrub|wetland)$"]({box});
  way["waterway"]({box});
  way["amenity"="parking"]({box});
);
out geom;"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--campus", choices=sorted(BOXES), default="atlanta")
    parser.add_argument("--endpoint", help="one Overpass endpoint instead of the list")
    parser.add_argument("--attempts", type=int, default=6,
                        help="rounds over the endpoints, waiting 30 s and doubling between them")
    args = parser.parse_args()

    box = ",".join(f"{v:.4f}" for v in BOXES[args.campus])
    query = QUERY.format(box=box)
    endpoints = (args.endpoint,) if args.endpoint else ENDPOINTS
    body = endpoint = None
    wait = 30.0
    with httpx.Client(timeout=180, headers={"User-Agent": "campus-map-builder/0.1"}) as client:
        # A busy server answers 502 and asks for exponential backoff.
        for attempt in range(args.attempts):
            for endpoint in endpoints:
                try:
                    response = client.post(endpoint, data={"data": query})
                except httpx.HTTPError as exc:
                    print(f"{endpoint}: {type(exc).__name__}", flush=True)
                    continue
                print(f"{endpoint}: HTTP {response.status_code}", flush=True)
                if response.status_code == 200:
                    body = response.content
                    break
            if body is not None or attempt == args.attempts - 1:
                break
            print(f"waiting {wait:.0f} s", flush=True)
            time.sleep(wait)
            wait *= 2
    if body is None:
        raise SystemExit("no Overpass endpoint answered; try again later")
    elements = json.loads(body)["elements"]

    out_dir = load_settings().data_dir / "maps" / "raw"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"{args.campus}-{stamp}.json"
    path.write_bytes(body)
    meta = {
        "campus": args.campus,
        "endpoint": endpoint,
        "query": query,
        "fetched_at": stamp,
        "sha256": hashlib.sha256(body).hexdigest(),
        "elements": len(elements),
        "license": "Open Database License; © OpenStreetMap contributors",
    }
    path.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"{len(elements)} elements -> {path}")


if __name__ == "__main__":
    main()
