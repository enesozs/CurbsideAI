# Curbside AI — Data Contract

Hard data semantics. When code and this file disagree, fix one of them explicitly; never let them drift. The source audit (`curbside inspect`, reports in `reports/audit/`) checked these semantics against real files.

## Populations

| `trip_source` | Population | Raw file pattern |
|---|---|---|
| `yellow` | Completed NYC yellow-taxi trips (vendor-submitted) | `yellow_tripdata_YYYY-MM.parquet` |
| `hvfhv` | Completed high-volume for-hire trips (Uber, Lyft) | `fhvhv_tripdata_YYYY-MM.parquet` |

The two populations differ in service, pricing and geography. They are stored, modelled and displayed per source. A combined "all" view must be labelled as a sum of two different populations.

Not covered: green taxis (optional later), other FHV, subway, bus, private cars, cycling.

## Window and dev slice

- **Analysis window:** 2023-01 → the latest published month. On 2026-11-14 the latest was 2026-09.
- **Source audit:** one 2024 month per source.
- **Dev/test fixtures:** a documented small subset with at least 2 adjacent days, plus both 2024 DST transition days (2024-03-10, 2024-11-03). It must be a fair sample of zones, not cherry-picked high-demand zones.

## Raw files and retention (bronze)

- Every download writes a manifest row to `data/manifests/` with these fields: URL, local path, SHA256, byte size, retrieval time (UTC), HTTP status. Manifests are committed; raw files are not.
- **Yellow raw:** keep.
- **HVFHV raw:** about 500 MB a month. Delete only after its silver partition is built and its reconciliation checks pass. It can be re-downloaded and re-verified through the manifest hash.
- **Zones:** keep `taxi_zones.zip` and the lookup CSV raw.

## Fields

Use the exact column names from each downloaded file. Names and casing change across years, and the 2025+ files add `cbd_congestion_fee`.

- **yellow:** `tpep_pickup_datetime`, `tpep_dropoff_datetime`, `PULocationID`, `DOLocationID`, `trip_distance`, `fare_amount`, `total_amount`, `congestion_surcharge`, `Airport_fee`/`airport_fee`, `cbd_congestion_fee` (2025+).
- **hvfhv:** `hvfhs_license_num`, `request_datetime`, `on_scene_datetime`, `pickup_datetime`, `dropoff_datetime`, `PULocationID`, `DOLocationID`, `trip_miles`, `trip_time`, `base_passenger_fare`, `driver_pay`, `cbd_congestion_fee` (2025+).
  - `on_scene_datetime` may be null for some licensees; the source audit reports the null share by licensee.

## Time semantics

- TLC timestamps are **naive local wall times** in America/New_York (confirmed by the source audit). Raw timestamps are preserved unchanged.
- Canonical key: a timezone-aware **UTC hour**. The local hour label, UTC offset and a DST `fold` flag are kept for display.
- **Spring-forward** (02:00–02:59 does not exist): flag any rows with such times and count them in the QA report. They are not silently shifted.
- **Fall-back** (01:00–01:59 occurs twice): naive times can't tell the two hours apart. Policy: the trips are split evenly between the two UTC hours and flagged. Whatever the choice, the two UTC hours must never merge into one repeated label.
- Rows whose pickup time falls outside the file's nominal month are flagged and counted. They are assigned by pickup time, not by file.

## Silver: validated trips

Location: `data/silver/trips/trip_source=<s>/month=<file month>/part.parquet` (zstd). Built by `curbside build`, which also writes per-file stats to `reports/qa/silver/<s>_<month>.json`.

Columns:
- `trip_source`, `source_month` (the file's month), `source_file_sha256`
- `pickup_ts_raw`, `dropoff_ts_raw`, `request_ts_raw` (hvfhv): naive local wall time, unchanged
- `pickup_local_hour`: `date_trunc('hour', pickup_ts_raw)`, with spring-forward rows moved +1 h (D4). UTC conversion happens in gold through the hour calendar (D2), never per row.
- `pickup_zone_id`, `dropoff_zone_id`, `trip_distance_miles`, `trip_duration_s` (dropoff − pickup)
- `wait_s` (hvfhv: pickup − request)
- `fare_usd`: base fare before surcharges and tips, comparable across sources. Yellow `fare_amount`; hvfhv `base_passenger_fare`.
- `total_amount_usd`, `payment_type` (yellow only), `hvfhs_license_num` (hvfhv only)
- `cbd_congestion_fee_usd` (null before 2025)
- `flags`: INTEGER bitmask. Names and bits are in `curbside.config.FLAG_NAMES`. `COUNT_EXCLUDE_MASK` and `STATS_EXCLUDE_MASK` apply the D8 rules.

Rules:
- Flag outliers rather than silently dropping them. Exclusions are audited with counts per rule.
- Trips have no unique ID. Don't deduplicate on an invented identifier; report suspected exact duplicates only.
- **Zone IDs 264 (Unknown) and 265 (Outside NYC):** kept in silver and counted in the QA report. They are excluded from maps and from zone-level forecasting targets. Any other unknown zone ID gets a validation flag.

## Gold: zone-hour

Key: `(utc_hour, pickup_zone_id, trip_source)`. Location: `data/gold/zone_hour/trip_source=<s>/month=<local month>/part.parquet`. `utc_hour` is a naive timestamp holding UTC.

Columns:
- `local_hour_label` (e.g. `2024-11-03 01:00 EST`), `utc_offset_min`, `fold`
- `trip_count`
- `median_wait_s` and `wait_n` (hvfhv only)
- `data_quality_status`, `source_coverage`

Rules:
- **Zero vs missing:** generate explicit zero rows only for hours inside a month that is verified complete for that source. Otherwise mark the hour missing.
- **Wait time (hvfhv):** `pickup_datetime − request_datetime` in seconds, aggregated by pickup zone and pickup hour.
  - Values that are negative, or above a documented cap, are flagged and excluded from the median.
  - Report the median with its sample size; never report a wait time from fewer than a documented minimum number of trips.
  - Wait time is a service-tension signal. It is **not** a congestion measure.

## Gold: origin–destination

Key: `(local_date, origin_zone_id, destination_zone_id, trip_source)`. Location: `data/gold/od_daily/trip_source=<s>/month=<local month>/part.parquet`.

Columns:
- `trip_count`
- `median_duration_s`, `median_distance_miles`, `median_fare_usd` (rows without stats-exclusion flags; fare only if > 0)
- `stats_n`

Rules:
- The local date is used rather than the UTC date, because a "day" means a New York day to readers.
- Origin is a polygon zone. Destination is a polygon zone or 265 ("Outside NYC").

An OD edge is an *observed aggregate of trips*, not a road segment.

## Forecast outputs

Columns:
- `task` (`A_day_ahead` | `B_lag_aware`)
- `issued_at_utc`, `information_cutoff_utc`, `target_hour_utc`
- `zone_id`, `trip_source`
- `model_id`, `feature_version`, `git_sha`
- `point_forecast`, `lower`, `upper`, `interval_level`
- `evaluation_fold` (Task A)

Task B forecasts are additionally frozen with a SHA256 before their target month is published.

## Quality report (generated by the pipeline, never by hand)

Per month and source:
- input rows, output rows, and rows per exclusion rule
- unknown zones and counts for IDs 264/265
- DST-affected rows, out-of-month timestamps
- null shares per key column (including `on_scene_datetime` by licensee)
- reconciliation of monthly zone totals against the NYC Open Data aggregate (`c5iv-bn4s`), within a documented tolerance

## Leakage policy

- For a forecast issued at `t`, only data observed and available at or before `t` under that task's cutoff may be used.
- Weather features must be **forecast vintages** issued before `t`. In practice that means Open-Meteo Previous Runs `*_previous_day2`. Observed weather, reanalysis and the stitched Historical Forecast series are for explanation only.
- Scalers, encoders and conformal calibration sets are fitted inside each training fold.

## Minimum invariant tests

- exact hourly bucketing
- DST fixtures for both 2024 transitions
- non-negative counts
- unique gold keys
- gold totals equal silver totals per month and source
- no feature timestamp later than the information cutoff
- zones 264/265 excluded from targets
- every gold zone ID exists in the zone geometry
- deterministic output for fixed inputs

## Privacy and ethics

Data are published by TLC at zone level. Don't attempt to reidentify passengers or drivers, or to reconstruct individual itineraries. Public artifacts ship aggregates only, never row-level trips.
