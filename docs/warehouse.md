# Spatial-temporal warehouse

The pipeline turns raw TLC monthly files into validated trips (silver) and analysis tables (gold), and checks itself on every run. Semantics are in [`DATA_CONTRACT.md`](DATA_CONTRACT.md).

## Run it

```bash
uv sync
uv run curbside fetch-zones
uv run curbside fetch-range            # 2023-01 → WINDOW_END, both sources; skips verified files
uv run curbside build-zones            # zone table + simplified web GeoJSON
uv run curbside build                  # raw → silver → gold, with invariant checks (raises on failure)
uv run curbside reconcile              # vs TLC's monthly aggregate (NYC Open Data c5iv-bn4s)
uv run curbside qa                     # writes reports/qa/qa_summary.json
uv run curbside prune-raw              # deletes HVFHV raw files that passed every gate (policy D11)
uv run pytest
```

Re-runs are incremental:
- Silver is rebuilt only when the raw file's SHA256 or `SILVER_VERSION` changes.
- Gold is cheap and rebuilt each run.
- To extend the window, change `WINDOW_END` in `src/curbside/config.py`.

## Layout

| Layer | Path | Grain | Committed? |
|---|---|---|---|
| Manifest | `data/manifests/downloads.csv` | one row per download: URL, SHA256, bytes, time, status | yes |
| Bronze | `data/raw/tlc/{yellow,hvfhv,zones}/`, `data/raw/opendata/`, `data/raw/openmeteo/` | as published | no |
| Silver | `data/silver/trips/trip_source=<s>/month=<file month>/part.parquet` | one row per raw trip, with flags | no |
| Gold | `data/gold/zone_hour/trip_source=<s>/month=<local month>/` | (UTC hour, zone, source), dense with explicit zeros | no |
| Gold | `data/gold/od_daily/trip_source=<s>/month=<local month>/` | (local date, origin, destination, source) | no |
| Gold | `data/gold/zones/zones.parquet`, `zones_web.geojson` | 263 zones | no |
| QA | `reports/qa/` (silver stats, gold checks, reconciliation, summary, weather coverage) | per month and source | yes |

## Invariants enforced on every build

- Silver row count equals raw row count. Nothing is dropped silently.
- Gold has exactly (calendar hours × 263 zones) rows with unique keys. The fall-back month has one extra hour per zone.
- Gold trip total equals silver counted rows in the zone universe, for every month and source.
- The hour calendar maps every local hour to UTC. Fall-back hours are split ceil/floor; spring-forward rows move +1 h.
- Web artifacts round-trip gold exactly (tests).

## Results (window 2023-01 → 2026-09)

| | Yellow taxi | Uber/Lyft (HVFHV) |
|---|---|---|
| Months | 45 | 45 |
| Raw rows | 161,781,947 | 904,724,544 |
| Pickups in gold (zone universe, counted) | 158,965,439 | 904,683,847 |
| Gold invariants | all pass | all pass |
| Counting rule (reconciliation-selected) | exclude negative-amount rows | all rows |
| Months within ±0.5% of TLC aggregate | 26 (12 within ±2%, 7 beyond) | 44 (2026-06: −2.15%) |
| Median monthly gap | 0.41% | 0.004% (79% of zone-months match exactly) |
| Schema versions | 4: `airport_fee` casing (2023-02), `cbd_congestion_fee` (2025-01), new `request_source` (2026-06) | 2: `cbd_congestion_fee` (2025-01) |

- **DST checks, 8 transitions:** the skipped spring hour holds 0 yellow and ≤0.4% of normal HVFHV pickups; the repeated fall hour holds 1.5–2.1× normal. Timestamps stay naive local wall time throughout the window.
- **Disk after pruning:** 44 HVFHV raw files deleted under policy D11; 2026-06 is kept because it does not reconcile. Raw 3.1 GB, silver 18 GB, gold 0.6 GB.
- **Build time:** the full build runs in about 1.5 min on 12 cores when silver is current; a cold silver rebuild takes about 10 min.
- **Not used yet:** yellow `request_source` (2026-06+) is undocumented and stays unused until TLC documents it.

## Known limitations

- Our monthly totals don't match TLC's aggregate exactly. The residual pattern is month-specific, which suggests the aggregate was built from a different file version. Months outside ±0.5% are listed in the QA report and on the app's Data Quality page. Their data is used as published, not adjusted.
- The newest month (2026-09) is provisional: the TLC page did not list it yet, and it differs strongly from the aggregate.
- Licensee codes (HV0003/HV0005) are not mapped to company names until TLC's dictionary is cited.
