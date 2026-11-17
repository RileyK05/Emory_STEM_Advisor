"""Check the campus map and the backend place index against each other.

Offline: reads frontend/public/maps/<campus>.json and app/places/<campus>.json
(both from `scripts.build_campus_map`) and prints what a person should look at:

- place ids in one file and not the other (the frontend could not draw a pin);
- places no text can find (no name that reads as a building), so an answer that
  names them shows no map;
- names two places share, which match neither;
- ids made from OSM ids, since those change if the OSM element is redrawn;
- with --texts, the places each given sentence finds, to try wording by hand.

From backend/:
    python -m scripts.manual.map_audit [--campus atlanta] [--texts "Where is White Hall?" ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from app.places import INDEX_DIR, index_from

REPO = Path(__file__).resolve().parents[3]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--campus", default="atlanta")
    parser.add_argument("--texts", nargs="*", default=[])
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    drawn = json.loads((REPO / "frontend/public/maps" / f"{args.campus}.json").read_text(encoding="utf-8"))
    data = json.loads((INDEX_DIR / f"{args.campus}.json").read_text(encoding="utf-8"))
    map_ids = {b["id"] for b in drawn["buildings"] if b.get("id")}
    index_ids = {p["id"] for p in data["places"]}
    print(f"survey {data['survey'].get('fetchedAt')} sha256 {str(data['survey'].get('sha256'))[:12]}")
    print(f"{len(index_ids)} places in the index, {len(map_ids)} named buildings drawn")
    if drawn["survey"] != data["survey"]:
        print("! the map and the index come from different surveys: rebuild both")
    for label, ids in (("in the index but not drawn", index_ids - map_ids),
                       ("drawn but not in the index", map_ids - index_ids)):
        if ids:
            print(f"! {label}: {sorted(ids)}")

    owners: dict[str, list[str]] = defaultdict(list)
    for place in data["places"]:
        for name in place["match"]:
            owners[name].append(place["id"])
    shared = {name: ids for name, ids in owners.items() if len(ids) > 1}
    unfindable = [p for p in data["places"] if not p["match"]]
    by_osm = [p for p in data["places"] if p["id"].startswith("osm-")]
    print(f"\n{len(unfindable)} places no text can find (they still draw):")
    for place in unfindable:
        print(f"  {place['id']}: {place['name']!r}")
    print(f"\n{len(shared)} names shared by two places (match neither):")
    for name, ids in sorted(shared.items()):
        print(f"  {name!r}: {ids}")
    print(f"\n{len(by_osm)} ids made from OSM ids:")
    for place in by_osm:
        print(f"  {place['id']}: {place['name']!r} {place['match']}")

    if args.texts:
        index = index_from(data)
        names = {p["id"]: p["name"] for p in data["places"]}
        print()
        for text in args.texts:
            found = index.places_in(text)
            print(f"{text!r} -> {[names[i] for i in found] or 'nothing'}")


if __name__ == "__main__":
    main()
