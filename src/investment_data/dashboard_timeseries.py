"""Build dashboard time-series datasets not covered by item_timeseries."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd
import pyarrow.parquet as pq

from .timeseries import _unit_price, _write_json_atomic, _write_parquet_atomic


ENTITY_GROWTH_COLUMNS = {
    "export_value_usd": "export_value",
    "import_value_usd": "import_value",
    "avg_daily_export_usd": "avg_daily_export",
}


def build_dashboard_timeseries(
    data_root: Path,
    *,
    progress: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    """Build summary, country, and item-country analytical datasets."""
    notify = progress or (lambda _: None)
    summary = build_trade_summary_timeseries(data_root)
    notify({"dataset": "trade-summary", "done": True, "rows": summary["monthly_rows"]})
    country = build_country_timeseries(data_root)
    notify({"dataset": "country", "done": True, "rows": country["monthly_rows"]})
    item_country = build_item_country_timeseries(data_root, progress=notify)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trade_summary": summary,
        "country": country,
        "item_country": item_country,
    }


def build_trade_summary_timeseries(data_root: Path) -> dict[str, object]:
    source_paths = sorted(
        (data_root / "local" / "backfill" / "trade-summary").glob("year=*/*.parquet")
    )
    if not source_paths:
        raise RuntimeError("수출입총괄 백필이 없습니다.")
    frame = pd.concat([pd.read_parquet(path) for path in source_paths], ignore_index=True)
    frame = _monthly_only(frame)
    frame["series_key"] = "KR"
    monthly = _build_entity_monthly(frame, key_columns=["series_key"], name_columns=[])
    quarterly = _build_entity_quarterly(monthly, key_columns=["series_key"], name_columns=[])
    monthly = monthly.drop(columns="series_key")
    quarterly = quarterly.drop(columns="series_key")

    output_root = data_root / "local" / "analytics" / "trade-summary-timeseries"
    monthly_path = output_root / "monthly.parquet"
    quarterly_path = output_root / "quarterly.parquet"
    _write_parquet_atomic(monthly, monthly_path)
    _write_parquet_atomic(quarterly, quarterly_path)
    manifest = _small_manifest(source_paths, monthly, quarterly, monthly_path, quarterly_path)
    manifest.update({"dataset": "trade-summary", "canonical_storage": "analytics-time-series"})
    _write_json_atomic(manifest, output_root / "manifest.json")
    return manifest


def build_country_timeseries(data_root: Path) -> dict[str, object]:
    source_paths = sorted(
        (data_root / "local" / "backfill" / "country-trade").glob("year=*/*.parquet")
    )
    if not source_paths:
        raise RuntimeError("국가별 수출입실적 백필이 없습니다.")
    frame = pd.concat([pd.read_parquet(path) for path in source_paths], ignore_index=True)
    frame = _monthly_only(frame)
    frame = frame[frame["country_code"].astype("string").str.fullmatch(r"[A-Z]{2}", na=False)]
    monthly = _build_entity_monthly(
        frame,
        key_columns=["country_code"],
        name_columns=["country_name"],
    )
    quarterly = _build_entity_quarterly(
        monthly,
        key_columns=["country_code"],
        name_columns=["country_name"],
    )

    output_root = data_root / "local" / "analytics" / "country-timeseries"
    monthly_path = output_root / "monthly.parquet"
    quarterly_path = output_root / "quarterly.parquet"
    _write_parquet_atomic(monthly, monthly_path)
    _write_parquet_atomic(quarterly, quarterly_path)
    manifest = _small_manifest(source_paths, monthly, quarterly, monthly_path, quarterly_path)
    manifest.update(
        {
            "dataset": "country",
            "countries": int(monthly["country_code"].nunique()),
            "canonical_storage": "analytics-time-series",
        }
    )
    _write_json_atomic(manifest, output_root / "manifest.json")
    return manifest


def update_trade_summary_timeseries(
    data_root: Path,
    rows: pd.DataFrame,
    *,
    requested_start_month: str | None = None,
    requested_end_month: str | None = None,
) -> dict[str, object]:
    """Replace fetched months in the national summary time series."""
    output_root = data_root / "local" / "analytics" / "trade-summary-timeseries"
    monthly_path = output_root / "monthly.parquet"
    manifest_path = output_root / "manifest.json"
    if not monthly_path.exists() or not manifest_path.exists():
        raise RuntimeError("초기 수출입총괄 시계열이 없습니다. build-dashboard-timeseries를 먼저 실행하세요.")

    replacement = _monthly_only(rows)
    updated_months = sorted(replacement["date"].astype(str).unique())
    if not updated_months:
        raise RuntimeError("업데이트할 수출입총괄 월 데이터가 없습니다.")
    existing = pd.read_parquet(monthly_path)
    base_columns = _entity_base_columns(existing, replacement, name_columns=[])
    retained = existing.loc[~existing["date"].astype(str).isin(updated_months), base_columns]
    combined = pd.concat([retained, replacement.loc[:, base_columns]], ignore_index=True)
    combined["series_key"] = "KR"
    monthly = _build_entity_monthly(combined, key_columns=["series_key"], name_columns=[])
    quarterly = _build_entity_quarterly(monthly, key_columns=["series_key"], name_columns=[])
    monthly = monthly.drop(columns="series_key")
    quarterly = quarterly.drop(columns="series_key")
    quarterly_path = output_root / "quarterly.parquet"
    _write_parquet_atomic(monthly, monthly_path)
    _write_parquet_atomic(quarterly, quarterly_path)
    return _update_small_manifest(
        manifest_path,
        monthly,
        quarterly,
        monthly_path,
        quarterly_path,
        rows,
        updated_months,
        requested_start_month,
        requested_end_month,
    )


def update_country_timeseries(
    data_root: Path,
    rows: pd.DataFrame,
    *,
    requested_start_month: str | None = None,
    requested_end_month: str | None = None,
) -> dict[str, object]:
    """Replace fetched months in the country time series."""
    output_root = data_root / "local" / "analytics" / "country-timeseries"
    monthly_path = output_root / "monthly.parquet"
    manifest_path = output_root / "manifest.json"
    if not monthly_path.exists() or not manifest_path.exists():
        raise RuntimeError("초기 국가별 시계열이 없습니다. build-dashboard-timeseries를 먼저 실행하세요.")

    replacement = _monthly_only(rows)
    replacement = replacement[
        replacement["country_code"].astype("string").str.fullmatch(r"[A-Z]{2}", na=False)
    ].copy()
    updated_months = sorted(replacement["date"].astype(str).unique())
    if not updated_months:
        raise RuntimeError("업데이트할 정상 국가별 월 데이터가 없습니다.")
    existing = pd.read_parquet(monthly_path)
    base_columns = _entity_base_columns(
        existing, replacement, name_columns=["country_name"]
    )
    retained = existing.loc[~existing["date"].astype(str).isin(updated_months), base_columns]
    combined = pd.concat([retained, replacement.loc[:, base_columns]], ignore_index=True)
    monthly = _build_entity_monthly(
        combined, key_columns=["country_code"], name_columns=["country_name"]
    )
    quarterly = _build_entity_quarterly(
        monthly, key_columns=["country_code"], name_columns=["country_name"]
    )
    quarterly_path = output_root / "quarterly.parquet"
    _write_parquet_atomic(monthly, monthly_path)
    _write_parquet_atomic(quarterly, quarterly_path)
    manifest = _update_small_manifest(
        manifest_path,
        monthly,
        quarterly,
        monthly_path,
        quarterly_path,
        rows,
        updated_months,
        requested_start_month,
        requested_end_month,
    )
    manifest["countries"] = int(monthly["country_code"].nunique())
    _write_json_atomic(manifest, manifest_path)
    return manifest


def build_item_country_timeseries(
    data_root: Path,
    *,
    progress: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    """Normalize the large HS10-country fact table one year at a time."""
    notify = progress or (lambda _: None)
    source_root = data_root / "local" / "backfill" / "item-country-trade"
    year_dirs = sorted(source_root.glob("year=*"))
    if not year_dirs:
        raise RuntimeError("품목×국가 수출입실적 백필이 없습니다.")

    output_root = data_root / "local" / "analytics" / "item-country-timeseries"
    fact_root = output_root / "hs10-monthly"
    total_rows = 0
    total_source_rows = 0
    excluded_rows = 0
    observed_chapters: set[str] = set()
    observed_countries: set[str] = set()
    observed_months: set[str] = set()
    output_files: list[Path] = []
    empty_source_files = 0

    columns = [
        "date",
        "hs_code",
        "product_name",
        "country_code",
        "country_name",
        "export_value_usd",
        "export_weight_kg",
        "import_value_usd",
        "import_weight_kg",
        "trade_balance_usd",
    ]
    for year_dir in year_dirs:
        all_source_paths = sorted(year_dir.glob("country=*/*.parquet"))
        source_paths = []
        for path in all_source_paths:
            schema_names = set(pq.ParquetFile(path).schema.names)
            if set(columns).issubset(schema_names):
                source_paths.append(path)
            else:
                empty_source_files += 1
        if not source_paths:
            continue
        frame = pd.concat(
            [pd.read_parquet(path, columns=columns) for path in source_paths],
            ignore_index=True,
        )
        total_source_rows += len(frame)
        valid = (
            frame["date"].astype("string").str.fullmatch(r"\d{4}-\d{2}", na=False)
            & frame["hs_code"].astype("string").str.fullmatch(r"\d{10}", na=False)
            & frame["country_code"].astype("string").str.fullmatch(r"[A-Z]{2}", na=False)
        )
        excluded_rows += int((~valid).sum())
        frame = frame.loc[valid].copy()
        frame["date"] = frame["date"].astype(str)
        frame["hs_code"] = frame["hs_code"].astype(str)
        frame["country_code"] = frame["country_code"].astype(str)
        frame["hs_chapter"] = frame["hs_code"].str[:2]

        keys = ["date", "hs_code", "country_code"]
        if frame.duplicated(keys).any():
            frame = (
                frame.groupby(keys, as_index=False, sort=False)
                .agg(
                    product_name=("product_name", "last"),
                    country_name=("country_name", "last"),
                    export_value_usd=("export_value_usd", "sum"),
                    export_weight_kg=("export_weight_kg", "sum"),
                    import_value_usd=("import_value_usd", "sum"),
                    import_weight_kg=("import_weight_kg", "sum"),
                    trade_balance_usd=("trade_balance_usd", "sum"),
                )
            )
            frame["hs_chapter"] = frame["hs_code"].str[:2]

        period = pd.PeriodIndex(frame["date"], freq="M")
        frame["year"] = period.year
        frame["quarter"] = period.strftime("%Y-Q%q")
        frame["days_in_period"] = period.days_in_month
        frame["period_status"] = "final"
        frame["hs_level"] = "hs10"
        frame["export_unit_price_usd_per_kg"] = _unit_price(
            frame["export_value_usd"], frame["export_weight_kg"]
        )
        frame["avg_daily_export_usd"] = (
            frame["export_value_usd"] / frame["days_in_period"]
        ).astype("Float64")

        final_columns = [
            "date",
            "year",
            "quarter",
            "hs_chapter",
            "hs_level",
            "hs_code",
            "product_name",
            "country_code",
            "country_name",
            "period_status",
            "days_in_period",
            "export_value_usd",
            "export_weight_kg",
            "export_unit_price_usd_per_kg",
            "avg_daily_export_usd",
            "import_value_usd",
            "import_weight_kg",
            "trade_balance_usd",
        ]
        year = year_dir.name.split("=", 1)[1]
        year_rows = 0
        for chapter, chapter_frame in frame.groupby("hs_chapter", sort=True):
            path = fact_root / f"year={year}" / f"chapter={chapter}" / "data.parquet"
            result = chapter_frame.loc[:, final_columns].sort_values(
                ["hs_code", "country_code", "date"]
            )
            _write_parquet_atomic(result, path)
            output_files.append(path)
            rows = len(result)
            year_rows += rows
            total_rows += rows
            observed_chapters.add(str(chapter))
        observed_countries.update(frame["country_code"].unique())
        observed_months.update(frame["date"].unique())
        notify(
            {
                "dataset": "item-country",
                "year": year,
                "rows": year_rows,
                "completed_years": len(observed_months) // 12,
            }
        )
        del frame

    manifest = {
        "dataset": "item-country",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "canonical_storage": "analytics-time-series",
        "archive_policy": "historical backfill frozen; future updates replace current-year partitions",
        "partitioning": ["year", "hs_chapter"],
        "growth_policy": "country-item growth is calculated when selected dashboard JSON is generated",
        "source_rows": total_source_rows,
        "rows": total_rows,
        "excluded_non_month_or_nonstandard_rows": excluded_rows,
        "empty_source_files": empty_source_files,
        "start_month": min(observed_months),
        "end_month": max(observed_months),
        "countries": len(observed_countries),
        "chapters": sorted(observed_chapters),
        "files": len(output_files),
        "bytes": sum(path.stat().st_size for path in output_files),
        "path": str(fact_root),
    }
    _write_json_atomic(manifest, output_root / "manifest.json")
    return manifest


def update_item_country_timeseries(
    data_root: Path,
    rows: pd.DataFrame,
    *,
    requested_start_month: str,
    requested_end_month: str,
) -> dict[str, object]:
    """Replace requested months in year/chapter HS10-country partitions."""
    output_root = data_root / "local" / "analytics" / "item-country-timeseries"
    fact_root = output_root / "hs10-monthly"
    manifest_path = output_root / "manifest.json"
    if not fact_root.exists() or not manifest_path.exists():
        raise RuntimeError("초기 품목×국가 시계열이 없습니다. build-dashboard-timeseries를 먼저 실행하세요.")

    requested_months = pd.period_range(requested_start_month, requested_end_month, freq="M")
    requested_month_strings = [str(period) for period in requested_months]
    frame, excluded_rows = _prepare_item_country_rows(rows)
    month_strings = sorted(frame["date"].astype(str).unique())
    if not month_strings:
        raise RuntimeError("업데이트할 정상 품목×국가 월 데이터가 없습니다.")
    unexpected = sorted(set(month_strings) - set(requested_month_strings))
    if unexpected:
        raise RuntimeError(f"요청기간 밖의 품목×국가 데이터가 반환되었습니다: {unexpected[:3]}")

    final_columns = _item_country_final_columns()
    touched_files = 0
    removed_files = 0
    observed_periods = pd.PeriodIndex(month_strings, freq="M")
    for year in sorted(set(observed_periods.year)):
        year_months = {month for month in month_strings if month.startswith(f"{year}-")}
        year_replacement = frame[frame["date"].astype(str).isin(year_months)]
        year_root = fact_root / f"year={year}"
        existing_chapters = {
            path.parent.name.split("=", 1)[1]
            for path in year_root.glob("chapter=*/data.parquet")
        }
        replacement_chapters = set(year_replacement["hs_chapter"].astype(str).unique())
        for chapter in sorted(existing_chapters | replacement_chapters):
            path = year_root / f"chapter={chapter}" / "data.parquet"
            if path.exists():
                existing = pd.read_parquet(path)
                retained = existing.loc[
                    ~existing["date"].astype(str).isin(year_months), final_columns
                ]
            else:
                retained = pd.DataFrame(columns=final_columns)
            replacement = year_replacement.loc[
                year_replacement["hs_chapter"].astype(str).eq(chapter), final_columns
            ]
            result = pd.concat([retained, replacement], ignore_index=True)
            result = result.sort_values(["hs_code", "country_code", "date"])
            duplicate = result.duplicated(["date", "hs_code", "country_code"], keep=False)
            if duplicate.any():
                raise RuntimeError("품목×국가 upsert 후 월·HS코드·국가 중복이 발생했습니다.")
            if result.empty:
                if path.exists():
                    path.unlink()
                    removed_files += 1
                continue
            _write_parquet_atomic(result, path)
            touched_files += 1

    output_files = sorted(fact_root.glob("year=*/chapter=*/data.parquet"))
    total_rows = sum(pq.ParquetFile(path).metadata.num_rows for path in output_files)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "canonical_storage": "analytics-time-series",
            "archive_policy": "historical backfill frozen; API updates are not saved separately",
            "rows": total_rows,
            "files": len(output_files),
            "bytes": sum(path.stat().st_size for path in output_files),
            "end_month": max(str(manifest.get("end_month", month_strings[-1])), month_strings[-1]),
            "last_update": {
                "mode": "direct-timeseries-upsert",
                "requested_start_month": requested_start_month,
                "requested_end_month": requested_end_month,
                "updated_months": month_strings,
                "api_rows": len(rows),
                "standard_hs10_country_rows": len(frame),
                "excluded_non_month_or_nonstandard_rows": excluded_rows,
                "touched_partition_files": touched_files,
                "removed_empty_partition_files": removed_files,
            },
        }
    )
    _write_json_atomic(manifest, manifest_path)
    return manifest


def _prepare_item_country_rows(rows: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    columns = [
        "date", "hs_code", "product_name", "country_code", "country_name",
        "export_value_usd", "export_weight_kg", "import_value_usd",
        "import_weight_kg", "trade_balance_usd",
    ]
    if rows.empty:
        return pd.DataFrame(columns=_item_country_final_columns()), 0
    missing = set(columns) - set(rows.columns)
    if missing:
        raise RuntimeError(f"품목×국가 API 응답에 필수 열이 없습니다: {sorted(missing)}")
    frame = rows.loc[:, columns].copy()
    valid = (
        frame["date"].astype("string").str.fullmatch(r"\d{4}-\d{2}", na=False)
        & frame["hs_code"].astype("string").str.fullmatch(r"\d{10}", na=False)
        & frame["country_code"].astype("string").str.fullmatch(r"[A-Z]{2}", na=False)
    )
    excluded = int((~valid).sum())
    frame = frame.loc[valid].copy()
    frame["date"] = frame["date"].astype(str)
    frame["hs_code"] = frame["hs_code"].astype(str)
    frame["country_code"] = frame["country_code"].astype(str)
    keys = ["date", "hs_code", "country_code"]
    if frame.duplicated(keys).any():
        frame = frame.groupby(keys, as_index=False, sort=False).agg(
            product_name=("product_name", "last"),
            country_name=("country_name", "last"),
            export_value_usd=("export_value_usd", "sum"),
            export_weight_kg=("export_weight_kg", "sum"),
            import_value_usd=("import_value_usd", "sum"),
            import_weight_kg=("import_weight_kg", "sum"),
            trade_balance_usd=("trade_balance_usd", "sum"),
        )
    frame["hs_chapter"] = frame["hs_code"].str[:2]
    period = pd.PeriodIndex(frame["date"], freq="M")
    frame["year"] = period.year
    frame["quarter"] = period.strftime("%Y-Q%q")
    frame["days_in_period"] = period.days_in_month
    frame["period_status"] = "final"
    frame["hs_level"] = "hs10"
    frame["export_unit_price_usd_per_kg"] = _unit_price(
        frame["export_value_usd"], frame["export_weight_kg"]
    )
    frame["avg_daily_export_usd"] = (
        frame["export_value_usd"] / frame["days_in_period"]
    ).astype("Float64")
    return frame.loc[:, _item_country_final_columns()], excluded


def _item_country_final_columns() -> list[str]:
    return [
        "date", "year", "quarter", "hs_chapter", "hs_level", "hs_code",
        "product_name", "country_code", "country_name", "period_status",
        "days_in_period", "export_value_usd", "export_weight_kg",
        "export_unit_price_usd_per_kg", "avg_daily_export_usd",
        "import_value_usd", "import_weight_kg", "trade_balance_usd",
    ]


def _monthly_only(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[
        frame["date"].astype("string").str.fullmatch(r"\d{4}-\d{2}", na=False)
    ].copy()


def _entity_base_columns(
    existing: pd.DataFrame,
    replacement: pd.DataFrame,
    *,
    name_columns: list[str],
) -> list[str]:
    candidates = [
        "date",
        "country_code",
        *name_columns,
        "export_value_usd",
        "import_value_usd",
        "trade_balance_usd",
        "export_count",
        "import_count",
    ]
    required = {"date", "export_value_usd", "import_value_usd", "trade_balance_usd"}
    missing = required - set(replacement.columns)
    if missing:
        raise RuntimeError(f"API 응답에 필수 열이 없습니다: {sorted(missing)}")
    return [column for column in candidates if column in existing.columns and column in replacement.columns]


def _update_small_manifest(
    manifest_path: Path,
    monthly: pd.DataFrame,
    quarterly: pd.DataFrame,
    monthly_path: Path,
    quarterly_path: Path,
    api_rows: pd.DataFrame,
    updated_months: list[str],
    requested_start_month: str | None,
    requested_end_month: str | None,
) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "start_month": str(monthly["date"].min()),
            "end_month": str(monthly["date"].max()),
            "monthly_rows": len(monthly),
            "quarterly_rows": len(quarterly),
            "canonical_storage": "analytics-time-series",
            "archive_policy": "historical backfill frozen; API updates are not saved separately",
            "last_update": {
                "mode": "direct-timeseries-upsert",
                "requested_start_month": requested_start_month,
                "requested_end_month": requested_end_month,
                "updated_months": updated_months,
                "api_rows": len(api_rows),
            },
            "outputs": {
                "monthly": {"path": str(monthly_path), "bytes": monthly_path.stat().st_size},
                "quarterly": {
                    "path": str(quarterly_path),
                    "bytes": quarterly_path.stat().st_size,
                },
            },
        }
    )
    _write_json_atomic(manifest, manifest_path)
    return manifest


def _build_entity_monthly(
    frame: pd.DataFrame,
    *,
    key_columns: list[str],
    name_columns: list[str],
) -> pd.DataFrame:
    numeric = [
        column
        for column in (
            "export_value_usd",
            "import_value_usd",
            "trade_balance_usd",
            "export_count",
            "import_count",
        )
        if column in frame.columns
    ]
    aggregations = {column: "sum" for column in numeric}
    aggregations.update({column: "last" for column in name_columns})
    monthly = frame.groupby(["date", *key_columns], as_index=False, sort=False).agg(aggregations)
    period = pd.PeriodIndex(monthly["date"], freq="M")
    monthly["year"] = period.year
    monthly["quarter"] = period.strftime("%Y-Q%q")
    monthly["days_in_period"] = period.days_in_month
    monthly["period_status"] = "final"
    monthly["avg_daily_export_usd"] = (
        monthly["export_value_usd"] / monthly["days_in_period"]
    ).astype("Float64")
    monthly["_period_ordinal"] = period.astype("int64")
    monthly = _add_entity_growth(
        monthly,
        key_columns=key_columns,
        period_lags={"mom": 1, "yoy": 12},
    )
    front = [
        "date",
        "year",
        "quarter",
        *key_columns,
        *name_columns,
        "period_status",
        "days_in_period",
        "export_value_usd",
        "avg_daily_export_usd",
        "export_value_mom_pct",
        "export_value_yoy_pct",
        "avg_daily_export_mom_pct",
        "avg_daily_export_yoy_pct",
        "import_value_usd",
        "import_value_mom_pct",
        "import_value_yoy_pct",
        "trade_balance_usd",
    ]
    tail = [column for column in ("export_count", "import_count") if column in monthly.columns]
    return monthly.loc[:, [*front, *tail]].sort_values([*key_columns, "date"]).reset_index(drop=True)


def _build_entity_quarterly(
    monthly: pd.DataFrame,
    *,
    key_columns: list[str],
    name_columns: list[str],
) -> pd.DataFrame:
    frame = monthly.copy()
    month_period = pd.PeriodIndex(frame["date"], freq="M")
    frame["quarter_period"] = month_period.asfreq("Q")
    coverage = pd.DataFrame(
        {
            "quarter_period": pd.PeriodIndex(sorted(frame["date"].unique()), freq="M").asfreq("Q"),
            "date": sorted(frame["date"].unique()),
        }
    )
    coverage["month_days"] = pd.PeriodIndex(coverage["date"], freq="M").days_in_month
    coverage = coverage.groupby("quarter_period", as_index=False).agg(
        months_available=("date", "nunique"), days_in_period=("month_days", "sum")
    )
    coverage["is_complete_period"] = coverage["months_available"] == 3

    numeric = [
        column
        for column in (
            "export_value_usd",
            "import_value_usd",
            "trade_balance_usd",
            "export_count",
            "import_count",
        )
        if column in frame.columns
    ]
    aggregations = {column: "sum" for column in numeric}
    aggregations.update({column: "last" for column in name_columns})
    aggregations["date"] = "nunique"
    quarterly = (
        frame.groupby(["quarter_period", *key_columns], as_index=False, sort=False)
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
    quarterly["avg_daily_export_usd"] = (
        quarterly["export_value_usd"] / quarterly["days_in_period"]
    ).astype("Float64")
    quarterly["_period_ordinal"] = (
        quarterly["quarter_period"].dt.year * 4 + quarterly["quarter_period"].dt.quarter - 1
    )
    quarterly = _add_entity_growth(
        quarterly,
        key_columns=key_columns,
        period_lags={"qoq": 1, "yoy": 4},
        require_complete=True,
    )
    front = [
        "period",
        "year",
        "quarter",
        *key_columns,
        *name_columns,
        "period_status",
        "is_complete_period",
        "months_available",
        "months_with_trade",
        "days_in_period",
        "export_value_usd",
        "avg_daily_export_usd",
        "export_value_qoq_pct",
        "export_value_yoy_pct",
        "avg_daily_export_qoq_pct",
        "avg_daily_export_yoy_pct",
        "import_value_usd",
        "import_value_qoq_pct",
        "import_value_yoy_pct",
        "trade_balance_usd",
    ]
    tail = [column for column in ("export_count", "import_count") if column in quarterly.columns]
    return quarterly.loc[:, [*front, *tail]].sort_values([*key_columns, "period"]).reset_index(drop=True)


def _add_entity_growth(
    frame: pd.DataFrame,
    *,
    key_columns: list[str],
    period_lags: dict[str, int],
    require_complete: bool = False,
) -> pd.DataFrame:
    result = frame.copy()
    arrays = [result[column].astype(str) for column in key_columns]
    arrays.append(result["_period_ordinal"])
    names = [*key_columns, "period"]
    index = pd.MultiIndex.from_arrays(arrays, names=names)
    lookup = result.loc[:, list(ENTITY_GROWTH_COLUMNS)].copy()
    lookup.index = index
    complete_lookup = None
    if require_complete:
        complete_lookup = pd.Series(result["is_complete_period"].to_numpy(), index=index)

    for suffix, lag in period_lags.items():
        lag_arrays = [result[column].astype(str) for column in key_columns]
        lag_arrays.append(result["_period_ordinal"] - lag)
        lag_index = pd.MultiIndex.from_arrays(lag_arrays, names=names)
        previous = lookup.reindex(lag_index)
        previous.index = result.index
        eligible = pd.Series(True, index=result.index)
        if require_complete and complete_lookup is not None:
            prior_complete = complete_lookup.reindex(lag_index)
            prior_complete.index = result.index
            eligible = (
                result["is_complete_period"].astype("boolean").fillna(False)
                & prior_complete.astype("boolean").fillna(False)
            )
        for column, prefix in ENTITY_GROWTH_COLUMNS.items():
            prior = previous[column]
            valid = eligible & prior.notna() & prior.ne(0) & result[column].notna()
            values = pd.Series(pd.NA, index=result.index, dtype="Float64")
            values.loc[valid] = result.loc[valid, column] / prior.loc[valid] - 1
            result[f"{prefix}_{suffix}_pct"] = values
    return result.drop(columns=["_period_ordinal"])


def _small_manifest(
    source_paths: list[Path],
    monthly: pd.DataFrame,
    quarterly: pd.DataFrame,
    monthly_path: Path,
    quarterly_path: Path,
) -> dict[str, object]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "archive_policy": "historical backfill frozen; future API updates write to time series",
        "source_files": len(source_paths),
        "start_month": str(monthly["date"].min()),
        "end_month": str(monthly["date"].max()),
        "day_average_basis": "calendar_days",
        "monthly_rows": len(monthly),
        "quarterly_rows": len(quarterly),
        "outputs": {
            "monthly": {"path": str(monthly_path), "bytes": monthly_path.stat().st_size},
            "quarterly": {"path": str(quarterly_path), "bytes": quarterly_path.stat().st_size},
        },
    }
