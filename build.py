#!/usr/bin/env python3
"""Build the route pack from ASTRA's Wanderland GeoPackage.

    python3 -I build.py work/wanderland_2056.gpkg <release-tag>

Reads the GeoPackage with sqlite3 and parses its WKB by hand (no GDAL needed), converts LV95 to WGS84 with
swisstopo's approximate formulas (about 1 m), simplifies every stage line to TOLERANCE metres and writes
out/routes.json.deflate (raw DEFLATE, which Apple's NSData .zlib reads) plus manifest.json (tracked, served by
GitHub Pages). Standard library only.
"""
import json
import sqlite3
import struct
import sys
import zlib
from pathlib import Path

FORMAT = 1
TOLERANCE = 20.0  # m, Douglas-Peucker in LV95
NOTICE = ("Source: Federal Roads Office FEDRO (ASTRA) / SchweizMobil, Wanderland (ch.astra.wanderland), "
          "licence Opendata BY: https://www.geo.admin.ch/en/general-terms-of-use-fsdi")
GRADE = {"easy": 1, "medium": 2, "difficult": 3}
DIRECTION = {"Identischer Hin- und Rückweg": 0, "Hinweg": 1, "Rückweg": 2}
KIND = {"national": "national", "regional": "regional", "local": "local", "handicap": "accessible"}


def wgs84(e, n):
    """LV95 to WGS84 degrees, swisstopo's approximate formulas (about 1 m)."""
    y = (e - 2_600_000) / 1e6
    x = (n - 1_200_000) / 1e6
    lon = 2.6779094 + 4.728982 * y + 0.791484 * y * x + 0.1306 * y * x * x - 0.0436 * y * y * y
    lat = 16.9023892 + 3.238272 * x - 0.270978 * y * y - 0.002528 * x * x - 0.0447 * y * y * x - 0.0140 * x * x * x
    return lat * 100 / 36, lon * 100 / 36


def wkb_lines(blob):
    """The LineStrings of a GeoPackage geometry blob as lists of (E, N), any of Z and M dropped."""
    assert blob[:2] == b"GP", "not a GeoPackage geometry"
    flags = blob[3]
    envelope = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[(flags >> 1) & 7]
    pos = 8 + envelope
    lines = []

    def read_geometry():
        nonlocal pos
        little = blob[pos] == 1
        order = "<" if little else ">"
        code = struct.unpack_from(order + "I", blob, pos + 1)[0]
        pos += 5
        if code & 0x20000000:  # EWKB SRID
            pos += 4
            code &= ~0x20000000
        base = code % 1000
        dims = 2 + {0: 0, 1: 1, 2: 1, 3: 2}[code // 1000]  # Z, M, ZM
        if base == 2:  # LineString
            n = struct.unpack_from(order + "I", blob, pos)[0]
            pos += 4
            pts = struct.unpack_from(order + "d" * (n * dims), blob, pos)
            pos += 8 * n * dims
            lines.append([(pts[i * dims], pts[i * dims + 1]) for i in range(n)])
        elif base in (5, 7):  # MultiLineString, GeometryCollection
            n = struct.unpack_from(order + "I", blob, pos)[0]
            pos += 4
            for _ in range(n):
                read_geometry()
        else:
            raise ValueError(f"unexpected geometry type {code}")

    read_geometry()
    return lines


def simplify(points, tolerance):
    """Douglas-Peucker, iterative."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = points[a]
        bx, by = points[b]
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        best, index = 0.0, -1
        for i in range(a + 1, b):
            px, py = points[i]
            if length2 == 0:
                d2 = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
                d2 = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if d2 > best:
                best, index = d2, i
        if best > tolerance * tolerance:
            keep[index] = True
            stack.append((a, index))
            stack.append((index, b))
    return [p for p, k in zip(points, keep) if k]


def chain(lines):
    """Joins the parts end to end where they touch (within 1 m); parts that do not touch stay separate."""
    parts = [list(line) for line in lines if len(line) > 1]
    if not parts:
        return []
    out = [parts.pop(0)]
    while parts:
        tail = out[-1][-1]
        for i, part in enumerate(parts):
            if near(part[0], tail):
                out[-1].extend(part[1:])
                break
            if near(part[-1], tail):
                out[-1].extend(reversed(part[:-1]))
                break
        else:
            out.append(parts.pop(0))
            continue
        parts.pop(i)
    return out


def near(a, b):
    return abs(a[0] - b[0]) < 1 and abs(a[1] - b[1]) < 1


def minutes(hours):
    return None if hours is None else round(float(hours) * 60)


def main(gpkg, release):
    db = sqlite3.connect(gpkg)
    routes = {}
    rows = []
    for (nr, name, start, end, kind, km, up, down, fwd, back, fitness, technique, direction, segmented) in db.execute(
            "select NrR, NameR, AOrt, ZOrt, Typ_TR, LaengeR, HoeheAufR, HoeheAbR, ZeitStZiR, ZeitZiStR, "
            "KonditionR, TechnikR, Abwicklung, has_segment from Route order by cast(NrR as integer), NrR"):
        key = (nr, DIRECTION[direction])
        routes[key] = [nr, name, start, end, KIND[kind], km, up, down, minutes(fwd), minutes(back),
                       GRADE[fitness], GRADE[technique], DIRECTION[direction], 0]
    stages = []
    points_in = points_out = 0
    for (nr, stage, start, end, km, up, down, fwd, back, fitness, technique, direction, geom) in db.execute(
            "select NrR, NrEtappe, NameS, NameZ, DistanzE, HoeheAufE, HoeheAbE, ZeitStZiE, ZeitZiStE, KonditionE, "
            "TechnikE, Abwicklung, geom from Etappe order by cast(NrR as integer), NrR, NrEtappe"):
        # A route split into an outward and a return row still has stages marked for both ways.
        route = routes.get((nr, DIRECTION[direction])) or next(r for k, r in routes.items() if k[0] == nr)
        route[13] += 1
        segments = []
        lats, lons = [], []
        for part in chain(wkb_lines(geom)):
            points_in += len(part)
            kept = simplify(part, TOLERANCE)
            points_out += len(kept)
            line = []
            for e, n in kept:
                lat, lon = wgs84(e, n)
                lats.append(lat)
                lons.append(lon)
                line.append(round(lat * 1e5))
                line.append(round(lon * 1e5))
            segments.append(line)
        bbox = [round(min(lats) * 1e5), round(min(lons) * 1e5), round(max(lats) * 1e5), round(max(lons) * 1e5)]
        stages.append([nr, stage, start, end, km, up, down, minutes(fwd), minutes(back), GRADE[fitness],
                       GRADE[technique], DIRECTION[direction], bbox, segments])
    for route in routes.values():
        if route[13] == 1:  # a single-stage route: its stage is the route, numbered 0
            for s in stages:
                if s[0] == route[0] and s[11] == route[12]:
                    s[1] = 0
    out = Path("out")
    out.mkdir(exist_ok=True)
    build = release.removeprefix("routes-")
    body = json.dumps({"build": build, "licence": NOTICE, "routes": list(routes.values()), "stages": stages},
                      ensure_ascii=False, separators=(",", ":")).encode()
    packed = zlib.compress(body, 9)[2:-4]  # raw DEFLATE: no zlib header, no Adler-32
    (out / "routes.json.deflate").write_bytes(packed)
    manifest = {
        "build": build, "format": FORMAT, "release": release,
        "base": f"https://github.com/kenix/swiss-hiking-routes/releases/download/{release}/",
        "licence": "Opendata BY", "attribution": "Source: ASTRA / SchweizMobil",
        "file": "routes.json.deflate", "bytes": len(packed), "routes": len(routes), "stages": len(stages),
    }
    Path("manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"{len(routes)} routes, {len(stages)} stages, {points_in} -> {points_out} points, "
          f"{len(body) / 1e6:.1f} MB json, {len(packed) / 1e6:.2f} MB deflate")


if __name__ == "__main__":
    main(*sys.argv[1:3])
