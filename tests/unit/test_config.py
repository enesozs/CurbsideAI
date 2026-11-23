import pytest

from curbside import config


def test_trip_url_uses_tlc_prefixes():
    assert config.trip_url("yellow", "2024-11").endswith("/trip-data/yellow_tripdata_2024-11.parquet")
    assert config.trip_url("hvfhv", "2024-11").endswith("/trip-data/fhvhv_tripdata_2024-11.parquet")


@pytest.mark.parametrize("bad", ["2024-1", "24-01", "2024-13", "2024/01", "", "2008-12"])
def test_validate_month_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        config.validate_month(bad)


def test_next_month_rolls_over_year():
    assert config.next_month("2024-11") == "2024-12"
    assert config.next_month("2024-12") == "2025-01"
