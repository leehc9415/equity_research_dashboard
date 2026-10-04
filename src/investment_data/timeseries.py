"""Build dashboard-ready monthly and quarterly item time series."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .api_catalog import ENDPOINTS
from .client import KcsClient
from .parser import parse_xml


VALUE_COLUMNS = [
    "export_value_usd",
    "export_weight_kg",
    "import_value_usd",
    "import_weight_kg",
]

MONTHLY_GROWTH_COLUMNS = {
    "export_value_usd": "export_value",
    "export_unit_price_usd_per_kg": "export_unit_price",
    "avg_daily_export_usd": "avg_daily_export",
}


def build_item_timeseries(data_root: Path) -> dict[str, object]:
    """Create HS10/HS6 monthly and quarterly analytical Parquet files."""
    source_root = data_root / "local" / "backfill" / "item-trade"
    source_paths = sorted(source_root.glob("year=*/*.parquet"))
    if not source_paths:
        raise RuntimeError("품목별 수출입실적 Parquet가 없습니다.")

    columns = ["date", "hs_code", "product_name", *VALUE_COLUMNS, "trade_balance_usd"]
    source = pd.concat(
        [pd.read_parquet(path, columns=columns) for path in source_paths],
        ignore_index=True,
    )
    monthly_mask = source["date"].astype("string").str.fullmatch(r"\d{4}-\d{2}", na=False)
    hs10_mask = source["hs_code"].astype("string").str.fullmatch(r"\d{10}", na=False)
    standard = source.loc[monthly_mask & hs10_mask].copy()
    available_months = sorted(standard["date"].dropna().astype(str).unique())

    output_root = data_root / "local" / "analytics" / "item-timeseries"
    output_root.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, dict[str, object]] = {}

    for level in ("hs10", "hs6"):
        monthly = build_monthly_timeseries(standard, level=level)
        quarterly = build_quarterly_timeseries(monthly, available_months=available_months)
        for frequency, frame in (("monthly", monthly), ("quarterly", quarterly)):
            path = output_root / f"{level}_{frequency}.parquet"
            temp_path = path.with_suffix(".parquet.tmp")
            frame.to_parquet(
                temp_path,
                index=False,
                compression="zstd",
                row_group_size=65_536,
            )
            temp_path.replace(path)
            outputs[f"{level}_{frequency}"] = {
                "path": str(path),
                "rows": len(frame),
                "bytes": path.stat().st_size,
            }

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_files": len(source_paths),
        "source_rows": len(source),
        "standard_monthly_hs10_rows": len(standard),
        "excluded_non_month_or_nonstandard_rows": len(source) - len(standard),
        "start_month": available_months[0],
        "end_month": available_months[-1],
        "day_average_basis": "calendar_days",
        "unit_price_formula": "sum(export_value_usd) / sum(export_weight_kg)",
        "growth_policy": "null when the exact comparison period is absent or zero",
        "quarter_policy": "partial quarters are retained but growth rates are null",
        "canonical_storage": "analytics-time-series",
        "archive_policy": "historical backfill frozen; API updates are not saved separately",
        "outputs": outputs,
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def fetch_and_update_item_timeseries(
    client: KcsClient,
    *,
    start_month: str,
    end_month: str,
    data_root: Path,
) -> dict[str, object]:
    """Fetch item rows in memory and upsert only the analytical time-series files."""
    endpoint = ENDPOINTS["item-trade"]
    xml = client.fetch(
        endpoint,
        {
            "strtYymm": start_month.replace("-", ""),
            "endYymm": end_month.replace("-", ""),
        },
    )
    parsed = parse_xml(xml, endpoint)
    return update_item_timeseries(
        data_root,
        parsed.frame,
        requested_start_month=start_month,
        requested_end_month=end_month,
    )


def update_item_timeseries(
    data_root: Path,
    items: pd.DataFrame,
    *,
    requested_start_month: str | None = None,
    requested_end_month: str | None = None,
) -> dict[str, object]:
    """Replace fetched months in HS10/HS6 files without changing the frozen backfill."""
    output_root = data_root / "local" / "analytics" / "item-timeseries"
    manifest_path = output_root / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError("초기 품목 시계열이 없습니다. build-item-timeseries를 먼저 실행하세요.")

    monthly_mask = items["date"].astype("string").str.fullmatch(r"\d{4}-\d{2}", na=False)
    hs10_mask = items["hs_code"].astype("string").str.fullmatch(r"\d{10}", na=False)
    standard = items.loc[monthly_mask & hs10_mask].copy()
    updated_months = sorted(standard["date"].dropna().astype(str).unique())
    if not updated_months:
        raise RuntimeError("업데이트할 정상 HS10 월 데이터가 없습니다.")

    outputs: dict[str, dict[str, object]] = {}
    for level in ("hs10", "hs6"):
        monthly_path = output_root / f"{level}_monthly.parquet"
        if not monthly_path.exists():
            raise RuntimeError(f"기존 시계열 파일이 없습니다: {monthly_path}")
        existing = pd.read_parquet(monthly_path)
        monthly = upsert_monthly_timeseries(existing, standard, level=level)
        available_months = sorted(monthly["date"].dropna().astype(str).unique())
        quarterly = build_quarterly_timeseries(monthly, available_months=available_months)

        quarterly_path = output_root / f"{level}_quarterly.parquet"
        _write_parquet_atomic(monthly, monthly_path)
        _write_parquet_atomic(quarterly, quarterly_path)
        for frequency, path, frame in (
            ("monthly", monthly_path, monthly),
            ("quarterly", quarterly_path, quarterly),
        ):
            outputs[f"{level}_{frequency}"] = {
                "path": str(path),
                "rows": len(frame),
                "bytes": path.stat().st_size,
            }

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "start_month": min(manifest.get("start_month", updated_months[0]), updated_months[0]),
            "end_month": max(manifest.get("end_month", updated_months[-1]), updated_months[-1]),
            "standard_monthly_hs10_rows": outputs["hs10_monthly"]["rows"],
            "canonical_storage": "analytics-time-series",
            "archive_policy": "historical backfill frozen; API updates are not saved separately",
            "last_update": {
                "mode": "direct-timeseries-upsert",
                "requested_start_month": requested_start_month,
                "requested_end_month": requested_end_month,
                "updated_months": updated_months,
                "api_rows": len(items),
                "standard_hs10_rows": len(standard),
            },
            "outputs": outputs,
        }
    )
    _write_json_atomic(manifest, manifest_path)
    return manifest


def upsert_monthly_timeseries(
    existing: pd.DataFrame, replacement_items: pd.DataFrame, *, level: str
) -> pd.DataFrame:
    """Replace complete months and recalculate exact monthly comparison metrics."""
    replacement = build_monthly_timeseries(replacement_items, level=level)
    months = set(replacement["date"].astype(str))
    retained = existing.loc[~existing["date"].astype(str).isin(months)].copy()
    base_columns = _monthly_base_columns(level)
    combined = pd.concat(
        [retained.loc[:, base_columns], replacement.loc[:, base_columns]],
        ignore_index=True,
    )
    duplicate = combined.duplicated(["date", "hs_code"], keep=False)
    if duplicate.any():
        raise RuntimeError("시계열 upsert 후 월·HS코드 중복이 발생했습니다.")
    return _calculate_monthly_metrics(combined, level=level)


def build_monthly_timeseries(items: pd.DataFrame, *, level: str) -> pd.DataFrame:
    """Aggregate standard HS10 input into a dashboard monthly series."""
    if level not in {"hs10", "hs6"}:
        raise ValueError("level은 hs10 또는 hs6이어야 합니다.")

    frame = items.copy()
    frame["date"] = frame["date"].astype("string")
    frame["hs_code"] = frame["hs_code"].astype("string")
    frame = frame[
        frame["date"].str.fullmatch(r"\d{4}-\d{2}", na=False)
        & frame["hs_code"].str.fullmatch(r"\d{10}", na=False)
    ].copy()
    frame["series_code"] = frame["hs_code"] if level == "hs10" else frame["hs_code"].str[:6]

    aggregations: dict[str, object] = {
        **{column: "sum" for column in VALUE_COLUMNS},
        "trade_balance_usd": "sum",
        "hs_code": "size",
    }
    if level == "hs10":
        aggregations["product_name"] = "last"

    monthly = (
        frame.groupby(["date", "series_code"], as_index=False, sort=False)
        .agg(aggregations)
        .rename(columns={"series_code": "hs_code", "hs_code": "source_rows"})
    )
    monthly["hs_level"] = level
    return _calculate_monthly_metrics(monthly, level=level)


def _calculate_monthly_metrics(monthly: pd.DataFrame, *, level: str) -> pd.DataFrame:
    monthly = monthly.copy()
    period = pd.PeriodIndex(monthly["date"], freq="M")
    monthly["year"] = period.year
    monthly["quarter"] = period.strftime("%Y-Q%q")
    monthly["days_in_period"] = period.days_in_month
    monthly["period_status"] = "final"
    monthly["export_unit_price_usd_per_kg"] = _unit_price(
        monthly["export_value_usd"], monthly["export_weight_kg"]
    )
    monthly["avg_daily_export_usd"] = (
        monthly["export_value_usd"] / monthly["days_in_period"]
    ).astype("Float64")
    monthly["_period_ordinal"] = period.astype("int64")
    monthly = _add_exact_growth(monthly, period_lags={"mom": 1, "yoy": 12})

    columns = [
        "date",
        "year",
        "quarter",
        "hs_level",
        "hs_code",
        *( ["product_name"] if level == "hs10" else [] ),
        "period_status",
        "days_in_period",
        "export_value_usd",
        "export_weight_kg",
        "export_unit_price_usd_per_kg",
        "avg_daily_export_usd",
        "export_value_mom_pct",
        "export_value_yoy_pct",
        "export_unit_price_mom_pct",
        "export_unit_price_yoy_pct",
        "avg_daily_export_mom_pct",
        "avg_daily_export_yoy_pct",
        "import_value_usd",
        "import_weight_kg",
        "trade_balance_usd",
        "source_rows",
    ]
    return monthly.loc[:, columns].sort_values(["hs_code", "date"]).reset_index(drop=True)


def _monthly_base_columns(level: str) -> list[str]:
    return [
        "date",
        "hs_level",
        "hs_code",
        *(["product_name"] if level == "hs10" else []),
        "export_value_usd",
        "export_weight_kg",
        "import_value_usd",
        "import_weight_kg",
        "trade_balance_usd",
        "source_rows",
    ]


def build_quarterly_timeseries(
    monthly: pd.DataFrame, *, available_months: list[str]
) -> pd.DataFrame:
    """Aggregate monthly series using weighted unit prices and exact quarter lags."""
    frame = monthly.copy()
    period = pd.PeriodIndex(frame["date"], freq="M")
    frame["quarter_period"] = period.asfreq("Q")

    available = pd.DataFrame({"date": available_months})
    available_period = pd.PeriodIndex(available["date"], freq="M")
    available["quarter_period"] = available_period.asfreq("Q")
    available["month_days"] = available_period.days_in_month
    coverage = (
        available.groupby("quarter_period", as_index=False)
        .agg(months_available=("date", "nunique"), days_in_period=("month_days", "sum"))
    )
    coverage["is_complete_period"] = coverage["months_available"] == 3

    aggregations: dict[str, object] = {
        **{column: "sum" for column in VALUE_COLUMNS},
        "trade_balance_usd": "sum",
        "source_rows": "sum",
        "date": "nunique",
    }
    if "product_name" in frame.columns:
        aggregations["product_name"] = "last"

    quarterly = (
        frame.groupby(["quarter_period", "hs_level", "hs_code"], as_index=False, sort=False)
        .agg(aggregations)
        .rename(columns={"date": "months_with_trade"})
        .merge(coverage, on="quarter_period", how="left", validate="many_to_one")
    )
    quarterly["period"] = quarterly["quarter_period"].astype(str).str.replace("Q", "-Q", regex=False)
    quarterly["year"] = quarterly["quarter_period"].dt.year
    quarterly["quarter"] = quarterly["quarter_period"].dt.quarter
    quarterly["period_status"] = quarterly["is_complete_period"].map(
        {True: "final", False: "partial"}
    )
    quarterly["export_unit_price_usd_per_kg"] = _unit_price(
        quarterly["export_value_usd"], quarterly["export_weight_kg"]
    )
    quarterly["avg_daily_export_usd"] = (
        quarterly["export_value_usd"] / quarterly["days_in_period"]
    ).astype("Float64")
    quarterly["_period_ordinal"] = (
        quarterly["quarter_period"].dt.year * 4 + quarterly["quarter_period"].dt.quarter - 1
    )
    quarterly = _add_exact_growth(
        quarterly,
        period_lags={"qoq": 1, "yoy": 4},
        require_complete=True,
    )

    columns = [
        "period",
        "year",
        "quarter",
        "hs_level",
        "hs_code",
        *( ["product_name"] if "product_name" in quarterly.columns else [] ),
        "period_status",
        "is_complete_period",
        "months_available",
        "months_with_trade",
        "days_in_period",
        "export_value_usd",
        "export_weight_kg",
        "export_unit_price_usd_per_kg",
        "avg_daily_export_usd",
        "export_value_qoq_pct",
        "export_value_yoy_pct",
        "export_unit_price_qoq_pct",
        "export_unit_price_yoy_pct",
        "avg_daily_export_qoq_pct",
        "avg_daily_export_yoy_pct",
        "import_value_usd",
        "import_weight_kg",
        "trade_balance_usd",
        "source_rows",
    ]
    return quarterly.loc[:, columns].sort_values(["hs_code", "period"]).reset_index(drop=True)


def _add_exact_growth(
    frame: pd.DataFrame,
    *,
    period_lags: dict[str, int],
    require_complete: bool = False,
) -> pd.DataFrame:
    result = frame.copy()
    index = pd.MultiIndex.from_arrays(
        [result["hs_code"].astype(str), result["_period_ordinal"]],
        names=["hs_code", "period"],
    )
    source_columns = list(MONTHLY_GROWTH_COLUMNS)
    lookup = result.loc[:, source_columns].copy()
    lookup.index = index
    complete_lookup = None
    if require_complete:
        complete_lookup = pd.Series(result["is_complete_period"].to_numpy(), index=index)

    for suffix, lag in period_lags.items():
        lag_index = pd.MultiIndex.from_arrays(
            [result["hs_code"].astype(str), result["_period_ordinal"] - lag],
            names=["hs_code", "period"],
        )
        previous = lookup.reindex(lag_index)
        previous.index = result.index
        eligible = pd.Series(True, index=result.index)
        if require_complete and complete_lookup is not None:
            previous_complete = complete_lookup.reindex(lag_index)
            previous_complete.index = result.index
            previous_complete = previous_complete.astype("boolean").fillna(False)
            eligible = result["is_complete_period"].astype("boolean").fillna(False) & previous_complete
        for column, output_prefix in MONTHLY_GROWTH_COLUMNS.items():
            prior = previous[column]
            valid = eligible & prior.notna() & prior.ne(0) & result[column].notna()
            output = pd.Series(pd.NA, index=result.index, dtype="Float64")
            output.loc[valid] = result.loc[valid, column] / prior.loc[valid] - 1
            result[f"{output_prefix}_{suffix}_pct"] = output
    return result.drop(columns=["_period_ordinal"])


def _unit_price(value: pd.Series, weight: pd.Series) -> pd.Series:
    result = pd.Series(pd.NA, index=value.index, dtype="Float64")
    valid = weight.notna() & weight.gt(0)
    result.loc[valid] = value.loc[valid] / weight.loc[valid]
    return result


def _write_parquet_atomic(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".parquet.tmp")
    frame.to_parquet(
        temp_path,
        index=False,
        compression="zstd",
        row_group_size=65_536,
    )
    temp_path.replace(path)


def _write_json_atomic(payload: dict[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temp_path.replace(path)
