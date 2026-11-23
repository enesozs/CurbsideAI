"""Forecast binaries align hour-for-hour with the observed binaries (SYNTHETIC data)."""

from datetime import datetime

import numpy as np
import polars as pl

from curbside import artifacts_forecast as af
from curbside import hours


def test_encode_aligns_with_month_grid_including_dst():
    cal = hours.calendar("2024-11", "2024-11").sort("utc_hour")
    utc = cal["utc_hour"].to_numpy()
    zones = [1, 2, 3]
    picks = [0, 50, len(utc) - 1]  # first hour, the second (EST) 1 AM on Nov 3, last hour
    df = pl.DataFrame(
        {
            "target_hour_utc": [utc[i].astype("datetime64[us]").astype(datetime) for i in picks],
            "zone_id": [1, 2, 3],
            "point_forecast": [10.4, 20.6, 30.0],
            "lower": [5.0, 15.0, 25.0],
            "upper": [15.0, 25.0, 35.0],
        }
    )
    arr = af._encode(df, utc, zones)
    assert arr.shape == (len(utc), 3, 3)
    assert arr[0, 0].tolist() == [10, 5, 15]
    assert arr[50, 1].tolist() == [21, 15, 25]
    assert arr[-1, 2].tolist() == [30, 25, 35]
    assert int((arr != af.NA).sum()) == 9  # everything else marked "no forecast"
    assert len(utc) == 30 * 24 + 1
    assert np.datetime64(utc[50], "h") == np.datetime64("2024-11-03T06", "h")
