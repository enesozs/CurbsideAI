import hashlib

import httpx
import pytest

from curbside import fetch

PAYLOAD = b"synthetic-parquet-bytes" * 1000


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_writes_file_and_manifest(tmp_path):
    client = _client(lambda req: httpx.Response(200, content=PAYLOAD, headers={"etag": '"abc"'}))
    manifest = tmp_path / "m.csv"
    dest = tmp_path / "raw" / "f.parquet"

    row = fetch.download(
        "https://x/f.parquet", dest, dataset="d", period="2024-11", client=client, manifest=manifest
    )

    assert dest.read_bytes() == PAYLOAD
    assert row.sha256 == hashlib.sha256(PAYLOAD).hexdigest()
    assert row.bytes == len(PAYLOAD) and row.etag == "abc"
    rows = fetch.read_manifest(manifest)
    assert len(rows) == 1 and rows[0]["sha256"] == row.sha256


def test_download_is_idempotent_when_hash_matches(tmp_path):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, content=PAYLOAD)

    client, manifest, dest = _client(handler), tmp_path / "m.csv", tmp_path / "f.parquet"
    fetch.download("https://x/f", dest, dataset="d", period="p", client=client, manifest=manifest)
    again = fetch.download("https://x/f", dest, dataset="d", period="p", client=client, manifest=manifest)

    assert again is None and len(calls) == 1
    assert len(fetch.read_manifest(manifest)) == 1


def test_download_redownloads_if_local_file_corrupted(tmp_path):
    client, manifest, dest = (
        _client(lambda r: httpx.Response(200, content=PAYLOAD)),
        tmp_path / "m.csv",
        tmp_path / "f",
    )
    fetch.download("https://x/f", dest, dataset="d", period="p", client=client, manifest=manifest)
    dest.write_bytes(b"corrupt")
    row = fetch.download("https://x/f", dest, dataset="d", period="p", client=client, manifest=manifest)
    assert row is not None and dest.read_bytes() == PAYLOAD


def test_truncated_download_leaves_no_file(tmp_path):
    def handler(req):
        return httpx.Response(200, content=PAYLOAD[:10], headers={"content-length": str(len(PAYLOAD))})

    dest = tmp_path / "f.parquet"
    with pytest.raises((OSError, httpx.HTTPError)):
        fetch.download(
            "https://x/f", dest, dataset="d", period="p", client=_client(handler), manifest=tmp_path / "m.csv"
        )
    assert not dest.exists()
    assert not dest.with_suffix(".parquet.part").exists()
    assert fetch.read_manifest(tmp_path / "m.csv") == []


def test_http_error_raises_and_records_nothing(tmp_path):
    client = _client(lambda r: httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        fetch.download(
            "https://x/f", tmp_path / "f", dataset="d", period="p", client=client, manifest=tmp_path / "m.csv"
        )
    assert fetch.read_manifest(tmp_path / "m.csv") == []


def test_probe_latest_stops_at_first_missing_month():
    available = {"2026-07", "2026-08", "2026-09"}

    def handler(req):
        month = req.url.path.rsplit("_", 1)[-1].removesuffix(".parquet")
        return httpx.Response(200 if month in available else 404)

    assert fetch.probe_latest("yellow", "2026-07", client=_client(handler)) == "2026-09"
    assert fetch.probe_latest("yellow", "2026-10", client=_client(handler)) is None
