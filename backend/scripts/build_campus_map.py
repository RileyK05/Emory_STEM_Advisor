"""Draw our own campus map file from the saved OpenStreetMap survey.

    python -m scripts.build_campus_map [--campus atlanta] [--raw FILE]

Reads the newest data/maps/raw/<campus>-*.json (from `scripts.fetch_campus_map`)
and writes frontend/public/maps/<campus>.json: our own compact format in metres
from a campus origin (x east, y south, so it maps straight onto SVG), with our
own layer names. Only named buildings inside the campus boundary get an id;
everything else in the survey box is kept as faded context.

It also writes backend/app/places/<campus>.json, the backend's place index: each
building id with the names that find it in an answer (`app.places.match_names`).

The file is derived from OpenStreetMap, so it is offered under the Open
Database License and every view of it shows "© OpenStreetMap contributors".
No model touches this data.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections.abc import Iterable, Sequence
from pathlib import Path

from app.config import load_settings
from app.places import INDEX_DIR, match_names

REPO = Path(__file__).resolve().parents[2]
NAMES = {"atlanta": "Emory University, Atlanta campus"}
# The point the campus boundary must contain, to pick Emory's main polygon.
ANCHORS = {"atlanta": (33.7925, -84.3240)}
# How far past the campus boundary's box faded context is kept, in metres.
CONTEXT_M = 250

ROAD_MAJOR = {"primary", "secondary", "tertiary", "primary_link", "secondary_link",
              "tertiary_link", "trunk"}
ROAD_MINOR = {"residential", "unclassified", "service", "living_street"}
PATH = {"footway", "path", "pedestrian", "steps", "cycleway", "track", "bridleway"}
LAWN = {("landuse", "grass"), ("landuse", "meadow"), ("landuse", "recreation_ground"),
        ("leisure", "park"), ("leisure", "garden")}
WOODS = {("landuse", "forest"), ("natural", "wood"), ("natural", "scrub"),
         ("natural", "wetland"), ("leisure", "nature_reserve")}
SPORT = {("leisure", "pitch"), ("leisure", "track"), ("leisure", "stadium"),
         ("leisure", "sports_centre")}

Point = tuple[float, float]


class Projection:
    """Metres from an origin; at campus size the flat error is far under a metre."""

    def __init__(self, lat0: float, lon0: float) -> None:
        self.lat0, self.lon0 = lat0, lon0
        self.kx = 111_320.0 * math.cos(math.radians(lat0))
        self.ky = 110_574.0

    def xy(self, lat: float, lon: float) -> Point:
        return ((lon - self.lon0) * self.kx, (self.lat0 - lat) * self.ky)

    def latlon(self, x: float, y: float) -> tuple[float, float]:
        return (self.lat0 - y / self.ky, self.lon0 + x / self.kx)


def simplify(points: Sequence[Point], tolerance: float) -> list[Point]:
    """Douglas-Peucker, iterative."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = points[a], points[b]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy) or 1e-9
        best, index = 0.0, -1
        for i in range(a + 1, b):
            px, py = points[i]
            dist = abs(dy * (px - ax) - dx * (py - ay)) / norm
            if dist > best:
                best, index = dist, i
        if best > tolerance:
            keep[index] = True
            stack += [(a, index), (index, b)]
    return [p for p, k in zip(points, keep) if k]


def simplify_ring(ring: Sequence[Point], tolerance: float) -> list[Point]:
    """Simplify an unclosed ring. Douglas-Peucker can't take a closed ring whole (its
    two ends are the same point), so split it at the point farthest from the start."""
    if len(ring) < 4:
        return list(ring)
    x0, y0 = ring[0]
    far = max(range(len(ring)), key=lambda i: math.hypot(ring[i][0] - x0, ring[i][1] - y0))
    first = simplify(ring[: far + 1], tolerance)
    second = simplify([*ring[far:], ring[0]], tolerance)
    return first + second[1:-1]


def flat(points: Iterable[Point]) -> list[float]:
    return [round(v, 1) for p in points for v in p]


def ring_area(ring: Sequence[Point]) -> float:
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]))) / 2


def centroid(ring: Sequence[Point]) -> Point:
    a = cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        cross = x1 * y2 - x2 * y1
        a += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(a) < 1e-9:
        xs, ys = zip(*ring)
        return (sum(xs) / len(xs), sum(ys) / len(ys))
    return (cx / (3 * a), cy / (3 * a))


def inside(point: Point, ring: Sequence[Point]) -> bool:
    x, y = point
    hit = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


def assemble(ways: list[list[Point]]) -> list[list[Point]]:
    """Join a multipolygon's member ways end to end into closed rings."""
    rings, pending = [], [list(w) for w in ways if len(w) >= 2]
    while pending:
        ring = pending.pop(0)
        while ring[0] != ring[-1]:
            for i, way in enumerate(pending):
                if way[0] == ring[-1]:
                    ring += way[1:]
                elif way[-1] == ring[-1]:
                    ring += way[::-1][1:]
                elif way[-1] == ring[0]:
                    ring = way[:-1] + ring
                elif way[0] == ring[0]:
                    ring = way[::-1][:-1] + ring
                else:
                    continue
                pending.pop(i)
                break
            else:
                break  # an open ring: the box cut it; keep what there is
        rings.append(ring)
    return rings


def element_rings(element: dict, proj: Projection) -> list[list[Point]]:
    """Outer rings of a closed way or multipolygon relation, projected, unclosed."""
    if element["type"] == "way":
        pts = [proj.xy(g["lat"], g["lon"]) for g in element.get("geometry", [])]
        return [pts[:-1]] if len(pts) >= 4 and pts[0] == pts[-1] else []
    if element["type"] == "relation":
        ways = [[proj.xy(g["lat"], g["lon"]) for g in m.get("geometry", [])]
                for m in element.get("members", [])
                if m.get("type") == "way" and m.get("role") in ("outer", "")]
        out = []
        for ring in assemble(ways):
            if len(ring) >= 4 and ring[0] == ring[-1]:
                out.append(ring[:-1])
        return out
    return []


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def newest_raw(campus: str) -> Path:
    found = sorted((load_settings().data_dir / "maps" / "raw").glob(f"{campus}-*Z.json"))
    if not found:
        raise SystemExit(f"no survey for {campus}; run scripts.fetch_campus_map first")
    return found[-1]


def build(raw: Path, campus: str) -> tuple[dict, list[dict]]:
    elements = json.loads(raw.read_text(encoding="utf-8"))["elements"]
    meta_path = raw.with_suffix(".meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    proj = Projection(*ANCHORS[campus])
    anchor = proj.xy(*ANCHORS[campus])

    boundary: list[list[Point]] = []
    for el in elements:
        tags = el.get("tags", {})
        if tags.get("amenity") == "university" and "Emory" in tags.get("name", ""):
            rings = element_rings(el, proj)
            if any(inside(anchor, r) for r in rings):
                boundary = max([boundary, rings], key=lambda rs: sum(map(ring_area, rs)))
    if not boundary:
        print("warning: no Emory boundary polygon contains the anchor; every building counts")

    def on_campus(point: Point) -> bool:
        return not boundary or any(inside(point, r) for r in boundary)

    xs = [v for r in boundary for v, _ in r] or [0.0]
    ys = [v for r in boundary for _, v in r] or [0.0]
    near = (min(xs) - CONTEXT_M, min(ys) - CONTEXT_M, max(xs) + CONTEXT_M, max(ys) + CONTEXT_M)

    def nearby(points: Iterable[Point]) -> bool:
        """Context is kept only close to campus, so the edges look whole but the
        file doesn't carry the whole neighbourhood."""
        return any(near[0] <= x <= near[2] and near[1] <= y <= near[3] for x, y in points)

    areas, lines, buildings, places = [], [], [], []
    used: dict[str, int] = {}
    for el in elements:
        tags = el.get("tags", {})
        if "building" in tags:
            for ring in element_rings(el, proj):
                if len(ring) < 3 or ring_area(ring) < 8:
                    continue
                label = centroid(ring)
                name = tags.get("name")
                campus_building = on_campus(label)
                if not campus_building and not nearby([label]):
                    continue
                item = {"rings": [flat(simplify_ring(ring, 0.4))],
                        "label": flat([label]), "area": round(ring_area(ring))}
                if name and campus_building:
                    # A name with no Latin letters (the Greek-letter houses) gets its OSM id.
                    base = slug(name)
                    if len(base) < 3:
                        base = f"osm-{el['type']}-{el['id']}"
                    used[base] = used.get(base, 0) + 1
                    item = {"id": base if used[base] == 1 else f"{base}-{used[base]}",
                            "name": name, "osm": f"{el['type']}/{el['id']}",
                            "at": [round(v, 6) for v in proj.latlon(*label)], **item}
                    alternates = tuple(part.strip() for key in ("official_name", "alt_name", "short_name")
                                       for part in tags.get(key, "").split(";") if part.strip())
                    local = tuple(part.strip() for part in tags.get("loc_name", "").split(";") if part.strip())
                    places.append({"id": item["id"], "name": name, "osm": item["osm"], "at": item["at"],
                                   "match": match_names(name, alternates=alternates, local=local)})
                elif not campus_building:
                    item["context"] = True
                buildings.append(item)
            continue
        kind = None
        pairs = set(tags.items())
        if pairs & LAWN:
            kind = "lawn"
        elif pairs & WOODS:
            kind = "woods"
        elif pairs & SPORT:
            kind = "sport"
        elif tags.get("natural") == "water":
            kind = "water"
        elif tags.get("amenity") == "parking":
            kind = "parking"
        elif tags.get("highway") == "pedestrian" and tags.get("area") == "yes":
            kind = "plaza"
        if kind:
            rings = [flat(simplify_ring(r, 0.8)) for r in element_rings(el, proj)
                     if len(r) >= 3 and nearby(r)]
            if rings:
                areas.append({"kind": kind, "rings": rings})
            continue
        highway = tags.get("highway")
        line_kind = ("road-major" if highway in ROAD_MAJOR else
                     "road" if highway in ROAD_MINOR else
                     "path" if highway in PATH else
                     "stream" if tags.get("waterway") in ("stream", "river", "canal") else None)
        if line_kind and el["type"] == "way":
            if tags.get("service") == "parking_aisle" or tags.get("footway") == "crossing":
                continue
            pts = [proj.xy(g["lat"], g["lon"]) for g in el.get("geometry", [])]
            if len(pts) >= 2 and nearby(pts):
                item = {"kind": line_kind, "points": flat(simplify(pts, 1.0))}
                if tags.get("name") and line_kind.startswith("road"):
                    item["name"] = tags["name"]
                lines.append(item)

    order = ["woods", "lawn", "sport", "water", "parking", "plaza"]
    areas.sort(key=lambda a: order.index(a["kind"]))
    line_order = ["stream", "path", "road", "road-major"]
    lines.sort(key=lambda line: line_order.index(line["kind"]))
    return {
        "campus": campus,
        "name": NAMES[campus],
        "attribution": "© OpenStreetMap contributors",
        "attributionUrl": "https://www.openstreetmap.org/copyright",
        "license": "Derived from OpenStreetMap; available under the Open Database License.",
        "survey": {"fetchedAt": meta.get("fetched_at"), "sha256": meta.get("sha256")},
        "origin": list(ANCHORS[campus]),
        "bounds": [round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))],
        "boundary": [flat(simplify_ring(r, 1.0)) for r in boundary],
        "areas": areas,
        "lines": lines,
        "buildings": buildings,
    }, places


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--campus", choices=sorted(NAMES), default="atlanta")
    parser.add_argument("--raw", type=Path, help="a saved survey instead of the newest one")
    args = parser.parse_args()
    raw = args.raw or newest_raw(args.campus)
    data, places = build(raw, args.campus)
    out = REPO / "frontend" / "public" / "maps" / f"{args.campus}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    index = {"campus": args.campus, "survey": data["survey"], "attribution": data["attribution"],
             "license": data["license"], "places": places}
    index_path = INDEX_DIR / f"{args.campus}.json"
    index_path.write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(places)} places ({sum(bool(p['match']) for p in places)} findable in text) -> {index_path}")
    named = [b for b in data["buildings"] if "id" in b]
    print(f"{len(data['buildings'])} buildings ({len(named)} named on campus), "
          f"{len(data['areas'])} areas, {len(data['lines'])} lines, "
          f"{out.stat().st_size // 1024} KB -> {out}")


if __name__ == "__main__":
    main()
