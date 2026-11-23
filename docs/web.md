# City Pulse (static web MVP)

A static single-page app that reads precomputed artifacts. There is no server.

## Run it locally

```bash
uv run curbside basemap        # one-time: NYC extract of the Protomaps daily build + fonts/sprites (~36 MB)
uv run curbside build-web      # gold → web/public/data (binaries + JSON)
cd web && npm install && npm run dev     # http://localhost:5173
npx playwright test             # smoke tests (desktop + mobile), uses the locally installed Chrome
```

`web/public/data/` and `web/public/basemap/` are generated and git-ignored; rebuild them with the two commands above.

## Artifacts (`web/public/data/`)

| File | Content |
|---|---|
| `meta.json` | window, zones in column order (id, name, borough, km², label point), per-month index (`start_utc`, `hours`, totals), encodings, DST-split hours, attribution |
| `zones.geojson` | 263 zones, coverage-simplified (40 ft tolerance, 98k → 24k vertices), feature `id` = LocationID |
| `zh/<source>/<YYYY-MM>.bin` | hourly pickups, uint16 little-endian, row-major [hour][zone]; hours are contiguous UTC from `start_utc` (≈380 KB per month and source) |
| `wait/<YYYY-MM>.bin` | Uber/Lyft median wait in seconds, uint16; 65535 = suppressed (fewer than 10 trips) |
| `daily/<source>.bin` | daily pickups by New York date, uint32 [day][zone] |
| `qa.json` | trimmed pipeline QA: reconciliation, flags, DST checks, schema versions |

Only aggregates are published, never trip rows. `tests/unit/test_artifacts.py` checks that the binaries reproduce gold exactly.

## Design decisions

- **Static binaries, not an API.** One month for both sources is under 1 MB. Scrubbing updates MapLibre feature-state, so no GeoJSON is re-parsed and the scrubber stays smooth.
- **Fixed, log-spaced classes** (1–2, 3–9, 10–29 … 1000+). Colours mean the same thing at every hour and month. Zero has its own neutral class; missing data is hatched.
- **Colours** come from the validated dataviz reference palette:
  - pickups use a blue sequential ramp
  - wait time uses a separate orange ramp, generated in OKLCH with monotonic lightness
  - yellow taxi = yellow and Uber/Lyft = violet; this pair passes the colour-vision-deficiency (CVD) checks in both themes
  - light-mode yellow is below 3:1 contrast, so every chart names the sources in text
- **Basemap:** self-hosted Protomaps extract (grayscale / black flavors). Zone fills sit above roads at about 80% opacity and below labels, so the street grid and place names stay readable.
- **Time:**
  - every label is New York time, from `Intl`
  - the fall-back night shows both "1:00 AM EDT" and "1:00 AM EST", with a note that trips are split evenly between them
  - the spring-forward night has 23 hours
- **Accessibility:**
  - the scrubber is a native range input
  - ←/→ step one hour, Shift+←/→ one day, Space plays or pauses
  - `prefers-reduced-motion` is respected
  - every map state has a sortable table alternative
  - the chart tooltips duplicate the chart values as text
- **Honest labels:** every legend states the unit ("completed yellow-taxi pickups / hour"), the aggregation, the time, the source and the missing-data treatment. The app never uses the word "traffic".

## Verified

- Playwright smoke tests on desktop (1440×900) and mobile (390×844) check: map renders, legend wording, keyboard stepping, measure and service toggles, table view, the Data Quality and Method pages, and dark theme. Screenshots were reviewed in both themes.
