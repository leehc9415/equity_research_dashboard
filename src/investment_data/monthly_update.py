"""Fetch one period and update every canonical dashboard time series."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from .api_catalog import ENDPOINTS
from .client import KcsClient
from .dashboard_timeseries import (
    update_country_timeseries,
    update_item_country_timeseries,
    update_trade_summary_timeseries,
)
from .parser import parse_xml
from .timeseries import _write_json_atomic, update_item_timeseries


def update_all_export_timeseries(
    client: KcsClient,
    *,
    start_month: str,
    end_month: str,
    data_root: Path,
    progress: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    """Fetch all four APIs in memory, then upsert only analytical files."""
    notify = progress or (lambda _: None)
    params = {
        "strtYymm": start_month.replace("-", ""),
        "endYymm": end_month.replace("-", ""),
    }

    fetched: dict[str, pd.DataFrame] = {}
    for slug in ("trade-summary", "item-trade", "country-trade"):
        endpoint = ENDPOINTS[slug]
        fetched[slug] = parse_xml(client.fetch(endpoint, params), endpoint).frame
        notify({"stage": "fetched", "dataset": slug, "rows": len(fetched[slug])})

    country_codes = _load_country_codes(data_root, fetched["country-trade"])
    item_country_parts: list[pd.DataFrame] = []
    empty_country_responses = 0
    endpoint = ENDPOINTS["item-country-trade"]
    for index, country_code in enumerate(country_codes, start=1):
        country_params = {**params, "cntyCd": country_code}
        frame = parse_xml(client.fetch(endpoint, country_params), endpoint).frame
        if frame.empty:
            empty_country_responses += 1
        else:
            item_country_parts.append(frame)
        if index % 25 == 0 or index == len(country_codes):
            notify(
                {
                    "stage": "fetching",
                    "dataset": "item-country-trade",
                    "completed_countries": index,
                    "total_countries": len(country_codes),
                    "rows_so_far": sum(len(part) for part in item_country_parts),
                }
            )
    fetched["item-country-trade"] = (
        pd.concat(item_country_parts, ignore_index=True) if item_country_parts else pd.DataFrame()
    )

    results = {
        "trade_summary": update_trade_summary_timeseries(
            data_root,
            fetched["trade-summary"],
            requested_start_month=start_month,
            requested_end_month=end_month,
        ),
        "item": update_item_timeseries(
            data_root,
            fetched["item-trade"],
            requested_start_month=start_month,
            requested_end_month=end_month,
        ),
        "country": update_country_timeseries(
            data_root,
            fetched["country-trade"],
            requested_start_month=start_month,
            requested_end_month=end_month,
        ),
        "item_country": update_item_country_timeseries(
            data_root,
            fetched["item-country-trade"],
            requested_start_month=start_month,
            requested_end_month=end_month,
        ),
    }
    for dataset, result in results.items():
        notify({"stage": "updated", "dataset": dataset, "rows": _result_rows(result)})

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "direct-timeseries-upsert",
        "requested_start_month": start_month,
        "requested_end_month": end_month,
        "raw_files_created": 0,
        "country_requests": len(country_codes),
        "empty_item_country_responses": empty_country_responses,
        "api_rows": {slug: len(frame) for slug, frame in fetched.items()},
        "datasets": results,
    }
    manifest_path = data_root / "local" / "analytics" / "last-update.json"
    _write_json_atomic(manifest, manifest_path)
    return manifest


def _load_country_codes(data_root: Path, latest_rows: pd.DataFrame) -> list[str]:
    codes: set[str] = set()
    path = data_root / "local" / "analytics" / "country-timeseries" / "monthly.parquet"
    if path.exists():
        existing = pd.read_parquet(path, columns=["country_code"])
        codes.update(existing["country_code"].dropna().astype(str))
    if "country_code" in latest_rows.columns:
        codes.update(latest_rows["country_code"].dropna().astype(str))
    result = sorted(code for code in codes if re.fullmatch(r"[A-Z]{2}", code))
    if not result:
        raise RuntimeError("품목×국가 조회에 사용할 국가 코드 목록이 없습니다.")
    return result


def _result_rows(result: dict[str, object]) -> int | None:
    for key in ("rows", "monthly_rows", "standard_monthly_hs10_rows"):
        value = result.get(key)
        if isinstance(value, int):
            return value
    return None
