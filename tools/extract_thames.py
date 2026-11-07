"""One-off: extract the River Thames centreline from OpenStreetMap.

Writes thames_route.json — a [[lat, lng], ...] polyline from Thames Head
(Kemble) to Southend-on-Sea at ~1 km waypoint spacing — for pasting into
journeys.json. Committed so the route can be regenerated or tweaked.

Usage (from row-app/): python3 tools/extract_thames.py [cached_overpass.json]
  With a path argument, that saved Overpass `out geom` result is used
  instead of fetching (handy when the public mirrors are struggling).
"""
import json
import math
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, ".")
from journey import haversine_m  # noqa: E402  (same maths as the app)

OVERPASS_ENDPOINTS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]
# OSM relations mark the primary channel with the "main_stream" role;
# prefer it (side channels confuse the joining walk), fall back to all ways.
QUERY_MAIN_STEM = """
[out:json][timeout:180];
rel["name"="River Thames"]["waterway"="river"]->.r;
way(r.r:"main_stream");
out geom;
"""
QUERY_ALL_WAYS = """
[out:json][timeout:180];
rel["name"="River Thames"]["waterway"="river"]->.r;
way(r.r);
out geom;
"""
KEMBLE = (51.6936, -2.0308)    # Thames Head
SOUTHEND = (51.5300, 0.7140)   # Southend-on-Sea
JOIN_TOLERANCE_M = 300  # 150 stalled at a 240 m gap near Culham
SPACING_M = 1000        # never more than this between waypoints
MAX_DEVIATION_M = 30    # ...and never cut a bend by more than this
END_SLACK_M = 2000      # keep estuary points this close to the closest approach


def fetch(query):
    data = urllib.parse.urlencode({"data": query}).encode()
    headers = {"User-Agent": "row-app-thames-extractor/1.0 (one-off personal hobby script)"}
    for url in OVERPASS_ENDPOINTS:
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=240) as resp:
                return json.load(resp)
        except Exception as exc:  # noqa: BLE001 — try the next mirror
            print(f"  {url} failed: {exc}", file=sys.stderr)
    sys.exit("All Overpass endpoints failed — try again in a few minutes.")


def join_ways(ways):
    """Order and join way geometries into one source->sea polyline."""
    segments = [[(n["lat"], n["lon"]) for n in w.get("geometry", [])] for w in ways]
    segments = [s for s in segments if len(s) >= 2]
    if not segments:
        sys.exit("No usable way geometries in the Overpass result.")
    segments.sort(key=lambda s: min(haversine_m(s[0], KEMBLE),
                                    haversine_m(s[-1], KEMBLE)))
    path = segments.pop(0)
    if haversine_m(path[-1], KEMBLE) < haversine_m(path[0], KEMBLE):
        path.reverse()
    attached = True
    while attached and segments:
        attached = False
        tail = path[-1]
        forward = None   # (distance, index)
        backward = None  # (distance, index)
        for i, seg in enumerate(segments):
            d_start = haversine_m(tail, seg[0])
            d_end = haversine_m(tail, seg[-1])
            if forward is None or d_start < forward[0]:
                forward = (d_start, i)
            if backward is None or d_end < backward[0]:
                backward = (d_end, i)
        # OSM draws waterways in flow direction. Prefer a segment that
        # STARTS at the tail; attach one reversed only when nothing flows
        # onward — otherwise a side channel that ENDS here drags the walk
        # back upstream and it stalls.
        if forward and forward[0] <= JOIN_TOLERANCE_M:
            seg = segments.pop(forward[1])
        elif backward and backward[0] <= JOIN_TOLERANCE_M:
            seg = segments.pop(backward[1])
            seg.reverse()
        else:
            break
        path.extend(seg[1:])
        attached = True
    print(f"  joined path: {len(path)} points; {len(segments)} ways unused "
          "(side channels and branches are expected leftovers)")
    return path


def _max_offset_m(path, a, b):
    """Furthest any point strictly between path[a] and path[b] lies from the
    straight line joining them (flat-earth approximation; fine at <=1 km)."""
    k = 111320.0
    c = math.cos(math.radians(path[a][0]))
    ax, ay = path[a][1] * k * c, path[a][0] * k
    dx, dy = path[b][1] * k * c - ax, path[b][0] * k - ay
    n = math.hypot(dx, dy) or 1.0
    return max((abs((q[1] * k * c - ax) * dy - (q[0] * k - ay) * dx) / n
                for q in path[a + 1:b]), default=0.0)


def simplify(path, spacing_m=SPACING_M, max_dev_m=MAX_DEVIATION_M):
    """Thin the polyline without straightening the river.

    Picking one point per 1 km of path replaces every meander with a chord
    and loses ~30 km of the Thames's length. Instead, extend each straight
    stretch as far as possible while it stays <= spacing_m long AND no
    skipped point sits more than max_dev_m off it.
    """
    keep = [0]
    a = 0
    while a < len(path) - 1:
        b = a + 1
        run = haversine_m(path[a], path[b])
        while (b + 1 < len(path)
               and run + haversine_m(path[b], path[b + 1]) <= spacing_m
               and _max_offset_m(path, a, b + 1) <= max_dev_m):
            run += haversine_m(path[b], path[b + 1])
            b += 1
        keep.append(b)
        a = b
    return [path[i] for i in keep]


def load_ways(cache_path=None):
    if cache_path:
        with open(cache_path) as f:
            ways = [el for el in json.load(f)["elements"] if el["type"] == "way"]
        print(f"{len(ways)} ways read from {cache_path}")
        return ways
    ways = [el for el in fetch(QUERY_MAIN_STEM)["elements"] if el["type"] == "way"]
    if not ways:
        print("main_stream role returned nothing; fetching all relation ways")
        ways = [el for el in fetch(QUERY_ALL_WAYS)["elements"] if el["type"] == "way"]
    print(f"{len(ways)} ways fetched from OSM")
    return ways


def main():
    ways = load_ways(sys.argv[1] if len(sys.argv) > 1 else None)
    if not ways:
        sys.exit("Overpass returned no ways for the River Thames relation.")
    path = join_ways(ways)
    to_kemble = [haversine_m(p, KEMBLE) for p in path]
    to_southend = [haversine_m(p, SOUTHEND) for p in path]
    if to_kemble[0] + to_southend[-1] > to_kemble[-1] + to_southend[0]:
        path.reverse()
        to_kemble.reverse()
        to_southend.reverse()
    # Start at the point nearest Thames Head. End at the LAST point that is
    # within END_SLACK_M of the closest approach to Southend, so a single
    # globally-nearest point can't cut the estuary short.
    i0 = min(range(len(path)), key=to_kemble.__getitem__)
    closest = min(to_southend[i0:])
    i1 = max(i for i in range(i0, len(path)) if to_southend[i] <= closest + END_SLACK_M)
    path = path[i0:i1 + 1]
    raw_km = sum(haversine_m(a, b) for a, b in zip(path, path[1:])) / 1000
    print(f"  trimmed path: {len(path)} points, {raw_km:.1f} km, "
          f"ends {closest / 1000:.1f} km from SOUTHEND")
    route = simplify(path)
    total = sum(haversine_m(a, b) for a, b in zip(route, route[1:]))
    max_gap = max(haversine_m(a, b) for a, b in zip(route, route[1:]))
    print(f"{len(route)} waypoints, {total / 1000:.1f} km, max gap {max_gap / 1000:.2f} km")
    if not 320000 <= total <= 380000:
        sys.exit("Length outside 320-380 km — inspect before using.")
    if max_gap > 3000:
        sys.exit("A gap exceeds 3 km — the joining walk broke somewhere.")
    rounded = [[round(lat, 5), round(lng, 5)] for lat, lng in route]
    with open("thames_route.json", "w") as f:
        json.dump(rounded, f)
    print("Wrote thames_route.json")


if __name__ == "__main__":
    main()
