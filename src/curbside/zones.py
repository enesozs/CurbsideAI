"""Taxi-zone geometry: zone table (area, label point) and a web-ready simplified GeoJSON.

Simplification uses shapely's coverage-aware ``coverage_simplify`` so shared borders between
neighbouring zones are simplified identically: no slivers or gaps on the choropleth.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import shapely

from curbside import config

SQFT_TO_KM2 = 0.09290304e-6
NATIVE_CRS = "EPSG:2263"


def zones_dir() -> Path:
    return config.GOLD / "zones"


def build_zones(
    zip_path: Path, lookup_csv: Path, out_dir: Path | None = None, tolerance_ft: float = 40.0
) -> dict[str, Any]:
    out_dir = out_dir or zones_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    gdf = gpd.read_file(f"zip://{zip_path}!taxi_zones/taxi_zones.shp")
    if gdf.crs is None or gdf.crs.to_epsg() != 2263:
        gdf = gdf.set_crs(NATIVE_CRS, allow_override=True) if gdf.crs is None else gdf.to_crs(NATIVE_CRS)
    gdf["LocationID"] = gdf["LocationID"].astype(int)
    if gdf["LocationID"].duplicated().any():
        # Merge multi-feature zones so each LocationID is one (multi)polygon.
        gdf = gdf.dissolve(by="LocationID", as_index=False, aggfunc="first")

    lookup = pd.read_csv(lookup_csv)
    lookup = lookup.rename(columns={"Borough": "borough", "Zone": "zone"})
    gdf = gdf[["LocationID", "geometry"]].merge(
        lookup[["LocationID", "borough", "zone", "service_zone"]], on="LocationID", how="left"
    )

    gdf["area_km2"] = gdf.geometry.area * SQFT_TO_KM2
    label_pts = gdf.geometry.representative_point().to_crs("EPSG:4326")
    gdf["label_lon"] = label_pts.x.round(6)
    gdf["label_lat"] = label_pts.y.round(6)

    simplified = shapely.coverage_simplify(gdf.geometry.values, tolerance=tolerance_ft)
    web = gpd.GeoDataFrame(
        gdf.drop(columns="geometry"), geometry=gpd.GeoSeries(simplified, crs=NATIVE_CRS)
    ).to_crs("EPSG:4326")
    web["geometry"] = shapely.set_precision(web.geometry.values, 1e-5)
    invalid_after = int((~web.geometry.is_valid).sum())
    if invalid_after:
        web["geometry"] = shapely.make_valid(web.geometry.values)

    table = gdf.drop(columns="geometry").sort_values("LocationID")
    table.to_parquet(out_dir / "zones.parquet", index=False)

    geojson_path = out_dir / "zones_web.geojson"
    features = json.loads(web.to_json(drop_id=True))
    for f in features["features"]:
        p = f["properties"]
        f["id"] = int(p["LocationID"])
        f["properties"] = {
            "id": int(p["LocationID"]),
            "zone": p["zone"],
            "borough": p["borough"],
            "area_km2": round(p["area_km2"], 4),
        }
    geojson_path.write_text(json.dumps(features, separators=(",", ":")))

    return {
        "zones": len(table),
        "area_km2_total": round(float(table["area_km2"].sum()), 2),
        "tolerance_ft": tolerance_ft,
        "vertices_before": int(shapely.get_num_coordinates(gdf.geometry.values).sum()),
        "vertices_after": int(shapely.get_num_coordinates(web.geometry.values).sum()),
        "invalid_after_simplify": invalid_after,
        "geojson_bytes": geojson_path.stat().st_size,
    }


def zone_ids(out_dir: Path | None = None) -> list[int]:
    t = pd.read_parquet((out_dir or zones_dir()) / "zones.parquet", columns=["LocationID"])
    return sorted(int(i) for i in t["LocationID"])
