"""Resumable, compressed backfill for large local datasets."""

from __future__ import annotations

import gzip
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .api_catalog import ENDPOINTS
from .client import KcsClient
from .parser import parse_xml


@dataclass(frozen=True)
class BackfillPart:
    endpoint: str
    start_month: str
    end_month: str
    rows: int
    raw_bytes: int
    raw_path: str
    parquet_path: str
    skipped: bool


def month_chunks(start_month: str, end_month: str) -> list[tuple[str, str]]:
    """Split an inclusive YYYY-MM range into calendar-year chunks."""
    start_year, start_number = map(int, start_month.split("-"))
    end_year, end_number = map(int, end_month.split("-"))
    chunks: list[tuple[str, str]] = []
    for year in range(start_year, end_year + 1):
        first = start_number if year == start_year else 1
        last = end_number if year == end_year else 12
        chunks.append((f"{year:04d}-{first:02d}", f"{year:04d}-{last:02d}"))
    return chunks


def backfill_endpoint(
    client: KcsClient,
    slug: str,
    *,
    start_month: str,
    end_month: str,
    output_root: Path,
    force: bool = False,
) -> list[BackfillPart]:
    endpoint = ENDPOINTS[slug]
    results: list[BackfillPart] = []
    for chunk_start, chunk_end in month_chunks(start_month, end_month):
        stem = f"{chunk_start.replace('-', '')}_{chunk_end.replace('-', '')}"
        raw_path = output_root / "raw" / "backfill" / slug / f"{stem}.xml.gz"
        parquet_path = (
            output_root
            / "local"
            / "backfill"
            / slug
            / f"year={chunk_start[:4]}"
            / f"{stem}.parquet"
        )
        if raw_path.exists() and parquet_path.exists() and not force:
            results.append(
                BackfillPart(
                    endpoint=slug,
                    start_month=chunk_start,
                    end_month=chunk_end,
                    rows=-1,
                    raw_bytes=raw_path.stat().st_size,
                    raw_path=str(raw_path),
                    parquet_path=str(parquet_path),
                    skipped=True,
                )
            )
            continue

        params = {
            "strtYymm": chunk_start.replace("-", ""),
            "endYymm": chunk_end.replace("-", ""),
        }
        if raw_path.exists() and not force:
            with gzip.open(raw_path, "rb") as stream:
                xml = stream.read()
        else:
            xml = client.fetch(endpoint, params)
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(raw_path, "wb", compresslevel=6) as stream:
                stream.write(xml)

        parsed = parse_xml(xml, endpoint)
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        parsed.frame.to_parquet(parquet_path, index=False, compression="zstd")
        result = BackfillPart(
            endpoint=slug,
            start_month=chunk_start,
            end_month=chunk_end,
            rows=len(parsed.frame),
            raw_bytes=raw_path.stat().st_size,
            raw_path=str(raw_path),
            parquet_path=str(parquet_path),
            skipped=False,
        )
        results.append(result)
        _append_manifest(output_root, result)
    return results


def backfill_item_country(
    client: KcsClient,
    *,
    start_month: str,
    end_month: str,
    country_codes: list[str],
    output_root: Path,
    force: bool = False,
    min_free_gb: float = 5.0,
):
    """Yield one resumable item-country part per country and calendar year."""
    endpoint = ENDPOINTS["item-country-trade"]
    for chunk_start, chunk_end in month_chunks(start_month, end_month):
        for country_code in sorted(set(country_codes)):
            stem = f"{chunk_start.replace('-', '')}_{chunk_end.replace('-', '')}_{country_code}"
            raw_path = (
                output_root / "raw" / "backfill" / endpoint.slug / f"year={chunk_start[:4]}" / f"{stem}.xml.gz"
            )
            parquet_path = (
                output_root
                / "local"
                / "backfill"
                / endpoint.slug
                / f"year={chunk_start[:4]}"
                / f"country={country_code}"
                / f"{stem}.parquet"
            )
            if raw_path.exists() and parquet_path.exists() and not force:
                yield BackfillPart(
                    endpoint=endpoint.slug,
                    start_month=chunk_start,
                    end_month=chunk_end,
                    rows=-1,
                    raw_bytes=raw_path.stat().st_size,
                    raw_path=str(raw_path),
                    parquet_path=str(parquet_path),
                    skipped=True,
                )
                continue

            free_gb = shutil.disk_usage(output_root).free / (1024**3)
            if free_gb < min_free_gb:
                raise RuntimeError(
                    f"디스크 여유 공간이 {free_gb:.2f}GB로 보호 기준 {min_free_gb:.2f}GB보다 작습니다."
                )
            params = {
                "strtYymm": chunk_start.replace("-", ""),
                "endYymm": chunk_end.replace("-", ""),
                "cntyCd": country_code,
            }
            if raw_path.exists() and not force:
                with gzip.open(raw_path, "rb") as stream:
                    xml = stream.read()
            else:
                xml = client.fetch(endpoint, params)
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                with gzip.open(raw_path, "wb", compresslevel=6) as stream:
                    stream.write(xml)

            parsed = parse_xml(xml, endpoint)
            parquet_path.parent.mkdir(parents=True, exist_ok=True)
            parsed.frame.to_parquet(parquet_path, index=False, compression="zstd")
            result = BackfillPart(
                endpoint=endpoint.slug,
                start_month=chunk_start,
                end_month=chunk_end,
                rows=len(parsed.frame),
                raw_bytes=raw_path.stat().st_size,
                raw_path=str(raw_path),
                parquet_path=str(parquet_path),
                skipped=False,
            )
            _append_manifest(output_root, result)
            yield result


def _append_manifest(output_root: Path, result: BackfillPart) -> None:
    path = output_root / "local" / "backfill" / "manifest.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **asdict(result),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
