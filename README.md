# Swiss hiking routes

The SchweizMobil hiking routes (Wanderland, ASTRA open data) packed so an app
can list them on the device: every route and stage with its official length,
heights, times, fitness and technique grade, and a simplified line. Trail Trove
reads it; nothing in here is specific to it.

Published as GitHub Release assets, one release per build. **The data is not
in this repository**: a release asset can be replaced, while binaries in the
history are in every clone for ever. What is here is the build script, the
manifest and the licence notice.

| File | What it is |
|---|---|
| `build.py` | Builds the pack from ASTRA's GeoPackage (Python standard library, no GDAL) |
| `manifest.json` | The current release, its file, size and counts |
| `ATTRIBUTION.md` | Source and licence |

## Using it

`manifest.json` is served from GitHub Pages:

```
https://kenix.github.io/swiss-hiking-routes/manifest.json
```

The pack is one file, `<base><file>` from the manifest, raw DEFLATE (no zlib
or gzip header; Apple's `NSData.decompressed(using: .zlib)` reads it) of

```json
{"build": "2026-11-12", "licence": "…", "routes": [...], "stages": [...]}
```

A **route** is `[nr, name, start, end, kind, km, up, down, minutesThere, minutesBack,
fitness, technique, direction, stages]`:

| Field | Meaning |
|---|---|
| `nr` | SchweizMobil route number as a string, as on the signposts ("1", "897") |
| `kind` | `national`, `regional`, `local` or `accessible` (ASTRA's "handicap") |
| `km`, `up`, `down` | official length in km and ascent / descent in m, whole route |
| `minutesThere`, `minutesBack` | official walking time each way; null for multi-day routes |
| `fitness`, `technique` | official grades 1 easy, 2 medium, 3 difficult |
| `direction` | 0 same way both ways, 1 outward only, 2 return only (three routes have one row per way) |
| `stages` | how many stage rows carry this `nr` and `direction` |

A **stage** is `[nr, stage, start, end, km, up, down, minutesThere, minutesBack,
fitness, technique, direction, bbox, lines]`, the same figures for the stage
alone. `stage` is 1-based; `0` means the route has no stages and this row is
the whole route. `bbox` is `[minLat, minLon, maxLat, maxLon]` in degrees · 1e5;
`lines` is a list of lines, each a flat `[lat, lon, lat, lon, …]` in degrees ·
1e5, simplified to 20 m (one line per stage in the 2026-09 data).

A reader skips fields it does not know, so a new field goes at the end.
`build` is the release name without `routes-`. The manifest's `format` is 1.

## Building a release

```sh
python3 -c "import urllib.request as u; u.urlretrieve('https://data.geo.admin.ch/ch.astra.wanderland/wanderland/wanderland_2056.gpkg', 'work/wanderland_2056.gpkg')"
python3 -I build.py work/wanderland_2056.gpkg routes-<yyyy-mm-dd>
gh release create routes-<yyyy-mm-dd> out/routes.json.deflate --notes-file ATTRIBUTION.md
git commit -am "release: routes-<yyyy-mm-dd>" && git push   # the manifest goes live with Pages
```

Upload the release before pushing the manifest: an app reads the manifest
first. ASTRA refreshes the dataset a few times a year (the STAC item's
`updated` says when); rebuild then.
