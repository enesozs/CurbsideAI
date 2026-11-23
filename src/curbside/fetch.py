"""Download official files with a reproducible manifest (URL, SHA256, size, time, status).

Downloads stream to a ``.part`` file and are hashed on the fly, so a crash never leaves a
half-written file that looks complete. Re-running is idempotent: a file whose hash already
matches the manifest is not downloaded again.
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from curbside import config

MANIFEST_FIELDS = [
    "dataset",
    "period",
    "url",
    "path",
    "sha256",
    "bytes",
    "retrieved_at_utc",
    "http_status",
    "last_modified",
    "etag",
]


@dataclass(frozen=True)
class ManifestRow:
    dataset: str
    period: str
    url: str
    path: str
    sha256: str
    bytes: int
    retrieved_at_utc: str
    http_status: int
    last_modified: str
    etag: str


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def manifest_path() -> Path:
    return config.MANIFESTS / "downloads.csv"


def read_manifest(path: Path | None = None) -> list[dict[str, str]]:
    path = path or manifest_path()
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def append_manifest(row: ManifestRow, path: Path | None = None) -> None:
    path = path or manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        if new:
            writer.writeheader()
        writer.writerow(asdict(row))


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(config.ROOT))
    except ValueError:
        return str(path)


def download(
    url: str,
    dest: Path,
    *,
    dataset: str,
    period: str,
    client: httpx.Client | None = None,
    manifest: Path | None = None,
) -> ManifestRow | None:
    """Download ``url`` to ``dest`` and record it. Returns None if already present and verified."""
    for prior in reversed(read_manifest(manifest)):
        if prior["url"] == url and dest.exists() and sha256_file(dest) == prior["sha256"]:
            return None

    own_client = client is None
    client = client or httpx.Client(
        headers={"User-Agent": config.USER_AGENT}, timeout=httpx.Timeout(30.0, read=300.0)
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    h = hashlib.sha256()
    size = 0
    try:
        with client.stream("GET", url, follow_redirects=True) as resp:
            resp.raise_for_status()
            with part.open("wb") as f:
                for block in resp.iter_bytes(1 << 20):
                    f.write(block)
                    h.update(block)
                    size += len(block)
            expected = resp.headers.get("content-length")
            if expected is not None and int(expected) != size:
                raise OSError(f"truncated download: got {size} of {expected} bytes from {url}")
            row = ManifestRow(
                dataset=dataset,
                period=period,
                url=url,
                path=_relative(dest),
                sha256=h.hexdigest(),
                bytes=size,
                retrieved_at_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                http_status=resp.status_code,
                last_modified=resp.headers.get("last-modified", ""),
                etag=resp.headers.get("etag", "").strip('"'),
            )
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    finally:
        if own_client:
            client.close()
    part.replace(dest)
    append_manifest(row, manifest)
    return row


def fetch_trips(source: config.TripSource, month: str, **kw) -> ManifestRow | None:
    return download(
        config.trip_url(source, month),
        config.trip_path(source, month),
        dataset=f"tlc_{source}",
        period=month,
        **kw,
    )


def fetch_zones(**kw) -> list[ManifestRow]:
    rows = []
    for name, url in config.ZONE_FILES.items():
        row = download(url, config.RAW / "tlc" / "zones" / name, dataset="tlc_zones", period="current", **kw)
        if row:
            rows.append(row)
    return rows


def probe_latest(
    source: config.TripSource, start: str, *, client: httpx.Client | None = None, max_months: int = 24
) -> str | None:
    """Return the latest month >= ``start`` whose file exists (HEAD 200), scanning forward."""
    own_client = client is None
    client = client or httpx.Client(headers={"User-Agent": config.USER_AGENT}, timeout=30.0)
    latest = None
    month = start
    try:
        for _ in range(max_months):
            resp = client.head(config.trip_url(source, month), follow_redirects=True)
            if resp.status_code != 200:
                break
            latest = month
            month = config.next_month(month)
    finally:
        if own_client:
            client.close()
    return latest
