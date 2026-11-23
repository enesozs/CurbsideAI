"""Zone graphs: polygon adjacency and observed OD flow summaries.

Adjacency = zones whose polygons share a border (or touch within 50 ft, which closes slivers
along simplified coastlines and street centrelines). The OD graph is an aggregate of observed
trips between zones — never a road network.
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np

from curbside import config, zones

ADJ_TOLERANCE_FT = 50.0


def adjacency(zip_path: Path | None = None) -> tuple[list[int], np.ndarray]:
    """(zone ids, row-normalised adjacency matrix W [Z, Z]) with zero diagonal."""
    zip_path = zip_path or config.RAW / "tlc" / "zones" / "taxi_zones.zip"
    gdf = gpd.read_file(f"zip://{zip_path}!taxi_zones/taxi_zones.shp").to_crs(zones.NATIVE_CRS)
    gdf["LocationID"] = gdf["LocationID"].astype(int)
    gdf = gdf.dissolve(by="LocationID", as_index=False).sort_values("LocationID")
    ids = gdf["LocationID"].tolist()
    buffered = gdf.geometry.buffer(ADJ_TOLERANCE_FT / 2)
    pairs = gpd.sjoin(
        gpd.GeoDataFrame({"i": range(len(ids))}, geometry=buffered, crs=gdf.crs),
        gpd.GeoDataFrame({"j": range(len(ids))}, geometry=buffered, crs=gdf.crs),
        predicate="intersects",
    )
    A = np.zeros((len(ids), len(ids)), dtype=np.float32)
    for i, j in zip(pairs["i"], pairs["j"], strict=True):
        if i != j:
            A[i, j] = 1.0
    deg = A.sum(axis=1, keepdims=True)
    W = np.divide(A, deg, out=np.zeros_like(A), where=deg > 0)
    return ids, W


def write_adjacency(out: Path | None = None) -> dict:
    ids, W = adjacency()
    out = out or zones.zones_dir() / "adjacency.json"
    neighbours = {str(z): [ids[j] for j in np.nonzero(W[i])[0]] for i, z in enumerate(ids)}
    out.write_text(json.dumps({"tolerance_ft": ADJ_TOLERANCE_FT, "neighbours": neighbours}))
    degree = (W > 0).sum(axis=1)
    return {
        "zones": len(ids),
        "isolated": [ids[i] for i in np.nonzero(degree == 0)[0]],
        "mean_degree": float(degree.mean()),
        "max_degree": int(degree.max()),
    }
