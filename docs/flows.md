# Flows

This part shows where trips go, and tests whether the zone network helps forecasting.

## Flows view

- **Data:** gold daily OD aggregated per month into weekday and weekend averages per OD pair (`curbside build-web` → `web/public/data/flows/`).
  - Pairs with fewer than 10 trips in a month are dropped from the map and table. They still carry about 0.3% of Uber/Lyft trips and about 2% of yellow trips.
  - The borough matrix is **exact**: it is computed before the threshold, and the share of trips kept is shown on the page.
- **Map:** curved schematic links between zone label points, in MapLibre (no deck.gl needed). Width is proportional to √(trips/day), so a few huge links don't hide the rest. Each link bends to the right of its direction of travel, so A→B and B→A are separate lines. The page always says "schematic links, not street routes".
- **Interaction:** service, weekday/weekend, month, top-N slider (25–500). Click a zone to show its flows **from** or **to** it.
- **"Outside NYC" (zone 265)** has no polygon. Those trips are listed in the table, never drawn. Airport → Outside NYC is the largest single link for Uber/Lyft.
- **Typical time:** the trip-weighted mean of daily median durations. It is labelled as such, because it is not an exact monthly median.
- **Accessibility:** the table is the accessible alternative to the map; the borough matrix prints its values in each cell.

## Graph features ablation (fed back into the forecaster)

- **Adjacency:** zones whose polygons touch within 50 ft (`curbside.graph.adjacency`). Mean degree is 5.0, maximum 12. Six zones are isolated: EWR, City Island, Liberty, Ellis and Governors islands, and Roosevelt Island.
- **Features:** adjacency-weighted means of neighbours' last-24-h pickups, same-hour-last-week pickups and 28-day level. All are computed from the same issue-time information as the zone's own features, so the leakage guarantees carry over.
- **Experiment:** retrain Task A with `BASE + GRAPH` features (`gbm_graph`) on the same four folds, then compare with the plain `gbm`. The data, seed and windows are identical. Command: `uv run curbside graph-ablation` → `reports/graph_ablation/`.

| Source | Fold | WAPE (GBM) | WAPE (+ graph) | WAPE high-volume zones | MAE sparse zones | 90% coverage (+ graph) |
|---|---|---|---|---|---|---|
| Yellow taxi | 2025Q4 | 18.9% | **18.6%** | 16.5% → 16.3% | 0.194 → 0.198 | 89.4% |
| Yellow taxi | 2026Q1 | 23.6% | **23.2%** | 20.9% → 20.5% | 0.231 → 0.229 | 90.2% |
| Yellow taxi | 2026Q2 | 19.0% | **18.2%** | 16.6% → 15.8% | 0.211 → 0.193 | 92.2% |
| Yellow taxi | 2026Q3 | 19.5% | **19.3%** | 17.0% → 16.9% | 0.172 → 0.174 | 90.3% |
| Uber/Lyft | 2025Q4 | 13.3% | **13.2%** | 12.6% → 12.4% | 0.037 → 0.036 | 87.8% |
| Uber/Lyft | 2026Q1 | 16.4% | **16.2%** | 16.0% → 15.8% | 0.035 → 0.033 | 88.7% |
| Uber/Lyft | 2026Q2 | 12.3% | **12.3%** | 11.6% → 11.6% | 0.054 → 0.054 | 92.2% |
| Uber/Lyft | 2026Q3 | 12.7% | **12.6%** | 12.1% → 12.0% | 0.056 → 0.054 | 91.0% |

**Result:** neighbour features improve citywide WAPE in **all 8 folds**.
- Yellow: mean 20.25% → 19.84%, about 2.0% relative.
- Uber/Lyft: 13.68% → 13.58%, about 0.7% relative.

The gain is real but small. Its likely source is spillover: demand displaced to the zone next door, around events or closures. Yellow benefits more because its demand is spatially concentrated. Interval coverage is unchanged.

**Decision:** graph features join the next model version (`feature_version` b1) at the next forecast refresh. The published forecasts stay as frozen; this is an ablation result, not a silent swap.
