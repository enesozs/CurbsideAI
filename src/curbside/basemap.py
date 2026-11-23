"""Self-hosted basemap: cut an NYC extract from the public Protomaps daily build.

Uses HTTP range requests against ``build.protomaps.com/<YYYYMMDD>.pmtiles`` (the same access
pattern as the ``pmtiles extract`` CLI) and the pure-Python ``pmtiles`` writer. Also fetches the
glyphs and sprites the Protomaps style needs, so the map has no runtime third-party dependency.
Tiles are an ODbL Produced Work of OpenStreetMap: attribution is shown in the app.
"""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from pmtiles.reader import Reader
from pmtiles.tile import zxy_to_tileid
from pmtiles.writer import Writer

from curbside import config, fetch

BUILDS_INDEX = "https://build-metadata.protomaps.dev/builds.json"
BUILD_URL = "https://build.protomaps.com/{key}"
ASSETS = "https://protomaps.github.io/basemaps-assets"
NYC_BBOX = (-74.30, 40.47, -73.65, 40.95)  # lon/lat; includes EWR
OUT_DIR = config.ROOT / "web" / "public" / "basemap"
FONTSTACKS = ["Noto Sans Regular", "Noto Sans Medium", "Noto Sans Italic"]
GLYPH_RANGES = ["0-255", "256-511", "8192-8447"]
SPRITES = ["light", "dark", "grayscale", "black"]


def latest_build(client: httpx.Client) -> dict[str, Any]:
    builds = client.get(BUILDS_INDEX).raise_for_status().json()
    return max(builds, key=lambda b: b["key"])


def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2**z
    x = int((lon + 180.0) / 360.0 * n)
    lat_r = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def tiles_in_bbox(bbox: tuple[float, float, float, float], z: int) -> list[tuple[int, int, int]]:
    x0, y0 = lonlat_to_tile(bbox[0], bbox[3], z)  # NW corner
    x1, y1 = lonlat_to_tile(bbox[2], bbox[1], z)  # SE corner
    return [(z, x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


def extract(
    out_path: Path | None = None,
    bbox: tuple[float, float, float, float] = NYC_BBOX,
    maxzoom: int = 14,
    log=print,
) -> dict[str, Any]:
    out_path = out_path or OUT_DIR / "nyc.pmtiles"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers={"User-Agent": config.USER_AGENT}, timeout=60.0) as client:
        build = latest_build(client)
        url = BUILD_URL.format(key=build["key"])

        @lru_cache(maxsize=8192)
        def get_bytes(offset: int, length: int) -> bytes:
            r = client.get(url, headers={"Range": f"bytes={offset}-{offset + length - 1}"})
            r.raise_for_status()
            if r.status_code != 206 or len(r.content) != length:
                raise OSError(f"bad range response {r.status_code} ({len(r.content)} of {length} bytes)")
            return r.content

        reader = Reader(get_bytes)
        src_header = reader.header()
        metadata = reader.metadata()
        wanted = sorted(
            (zxy_to_tileid(z, x, y), (z, x, y))
            for z in range(maxzoom + 1)
            for (z, x, y) in tiles_in_bbox(bbox, z)
        )
        log(f"basemap: {len(wanted)} tiles z0-{maxzoom} from {build['key']}")
        tmp = out_path.with_suffix(".pmtiles.part")
        written = 0
        with tmp.open("wb") as f:
            writer = Writer(f)
            for i, (tid, (z, x, y)) in enumerate(wanted):
                data = reader.get(z, x, y)
                if data:
                    writer.write_tile(tid, data)
                    written += 1
                if i and i % 250 == 0:
                    log(f"  {i}/{len(wanted)}")
            header = {
                "tile_type": src_header["tile_type"],
                "tile_compression": src_header["tile_compression"],
                "min_lon_e7": int(bbox[0] * 1e7),
                "min_lat_e7": int(bbox[1] * 1e7),
                "max_lon_e7": int(bbox[2] * 1e7),
                "max_lat_e7": int(bbox[3] * 1e7),
                "center_zoom": 11,
                "center_lon_e7": int((bbox[0] + bbox[2]) / 2 * 1e7),
                "center_lat_e7": int((bbox[1] + bbox[3]) / 2 * 1e7),
            }
            metadata = {
                **metadata,
                "curbside_extract": {"source_build": build, "bbox": bbox, "maxzoom": maxzoom},
            }
            writer.finalize(header, metadata)
        tmp.replace(out_path)

    sha = fetch.sha256_file(out_path)
    fetch.append_manifest(
        fetch.ManifestRow(
            dataset="protomaps_basemap_extract",
            period=build["key"],
            url=url,
            path=str(out_path.relative_to(config.ROOT)),
            sha256=sha,
            bytes=out_path.stat().st_size,
            retrieved_at_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            http_status=206,
            last_modified=build.get("uploaded", ""),
            etag=f"b3sum:{build.get('b3sum', '')}",
        )
    )
    return {"build": build["key"], "tiles": written, "bytes": out_path.stat().st_size, "sha256": sha}


def fetch_assets(out_dir: Path | None = None) -> dict[str, int]:
    """Glyph ranges and sprites for the Protomaps style (fonts: SIL OFL; sprites: CC0)."""
    out_dir = out_dir or OUT_DIR
    files = [f"fonts/{fs}/{rg}.pbf" for fs in FONTSTACKS for rg in GLYPH_RANGES]
    for name in SPRITES:
        for scale in ("", "@2x"):
            files += [f"sprites/v4/{name}{scale}.json", f"sprites/v4/{name}{scale}.png"]
    n = 0
    with httpx.Client(headers={"User-Agent": config.USER_AGENT}, timeout=60.0) as client:
        for rel in files:
            dest = out_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            r = client.get(f"{ASSETS}/{rel}".replace(" ", "%20"))
            r.raise_for_status()
            dest.write_bytes(r.content)
            n += 1
    digest = hashlib.sha256(b"".join((out_dir / rel).read_bytes() for rel in files)).hexdigest()
    return {"files": n, "combined_sha256": int(digest[:8], 16)}
