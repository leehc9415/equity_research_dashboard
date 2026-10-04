"""Build a reconciled monthly HS6 dataset from the downloaded HS detail."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


VALUE_COLUMNS = [
    "export_value_usd",
    "export_weight_kg",
    "import_value_usd",
    "import_weight_kg",
]


def aggregate_hs6_year(
    items: pd.DataFrame,
    summary: pd.DataFrame,
    *,
    known_hs10: set[str],
) -> pd.DataFrame:
    monthly = items[items["date"].astype(str).str.fullmatch(r"\d{4}-\d{2}")].copy()
    monthly["hs_code"] = monthly["hs_code"].astype(str)
    lengths = monthly["hs_code"].str.len()

    standard = monthly[lengths == 10].copy()
    standard["hs6_code"] = standard["hs_code"].str[:6]
    standard["source_hs10_rows"] = 1
    standard["source_nonstandard_rows"] = 0

    residual = monthly[lengths.isin([6, 9])].copy()
    residual["candidate_hs10"] = residual["hs_code"].where(lengths == 9) + "0"
    six_is_known = residual["hs_code"].isin({code[:6] for code in known_hs10})
    nine_is_known = residual["candidate_hs10"].isin(known_hs10)
    residual["hs6_code"] = "UNMAPPED_HS"
    residual.loc[(lengths == 6) & six_is_known, "hs6_code"] = residual["hs_code"]
    residual.loc[(lengths == 9) & nine_is_known, "hs6_code"] = residual["candidate_hs10"].str[:6]
    residual["source_hs10_rows"] = 0
    residual["source_nonstandard_rows"] = 1

    detail = pd.concat([standard, residual], ignore_index=True, sort=False)
    grouped = (
        detail.groupby(["date", "hs6_code"], as_index=False)[
            VALUE_COLUMNS + ["source_hs10_rows", "source_nonstandard_rows"]
        ]
        .sum()
    )
    grouped["data_status"] = "mapped"
    grouped.loc[grouped["hs6_code"] == "UNMAPPED_HS", "data_status"] = "unmapped"

    monthly_summary = summary[
        summary["date"].astype(str).str.fullmatch(r"\d{4}-\d{2}")
    ].copy()
    allocated = grouped.groupby("date", as_index=False)[
        ["export_value_usd", "import_value_usd"]
    ].sum()
    gaps = monthly_summary.merge(allocated, on="date", suffixes=("_summary", "_allocated"))
    gaps["export_value_usd"] = (
        gaps["export_value_usd_summary"] - gaps["export_value_usd_allocated"]
    )
    gaps["import_value_usd"] = (
        gaps["import_value_usd_summary"] - gaps["import_value_usd_allocated"]
    )
    gaps = gaps[(gaps["export_value_usd"] != 0) | (gaps["import_value_usd"] != 0)]
    adjustments = pd.DataFrame(
        {
            "date": gaps["date"],
            "hs6_code": "UNALLOCATED_TO_HS",
            "export_value_usd": gaps["export_value_usd"],
            "export_weight_kg": pd.array([pd.NA] * len(gaps), dtype="Int64"),
            "import_value_usd": gaps["import_value_usd"],
            "import_weight_kg": pd.array([pd.NA] * len(gaps), dtype="Int64"),
            "source_hs10_rows": 0,
            "source_nonstandard_rows": 0,
            "data_status": "reconciliation_adjustment",
        }
    )

    result = pd.concat([grouped, adjustments], ignore_index=True, sort=False)
    for column in VALUE_COLUMNS + ["source_hs10_rows", "source_nonstandard_rows"]:
        result[column] = result[column].astype("Int64")
    result["export_unit_price_usd_per_kg"] = _unit_price(
        result["export_value_usd"], result["export_weight_kg"]
    )
    result["import_unit_price_usd_per_kg"] = _unit_price(
        result["import_value_usd"], result["import_weight_kg"]
    )
    result["status"] = "final"
    columns = [
        "date",
        "hs6_code",
        "data_status",
        "status",
        "export_value_usd",
        "export_weight_kg",
        "export_unit_price_usd_per_kg",
        "import_value_usd",
        "import_weight_kg",
        "import_unit_price_usd_per_kg",
        "source_hs10_rows",
        "source_nonstandard_rows",
    ]
    return result.loc[:, columns].sort_values(["date", "hs6_code"]).reset_index(drop=True)


def build_hs6_dataset(data_root: Path) -> dict[str, object]:
    item_root = data_root / "local" / "backfill" / "item-trade"
    summary_root = data_root / "local" / "backfill" / "trade-summary"
    output_root = data_root / "local" / "derived" / "hs6"
    output_root.mkdir(parents=True, exist_ok=True)

    item_paths = sorted(item_root.glob("year=*/*.parquet"))
    if not item_paths:
        raise RuntimeError("품목별 백필 Parquet가 없습니다.")

    known_hs10: set[str] = set()
    for path in item_paths:
        codes = pd.read_parquet(path, columns=["hs_code"])["hs_code"].dropna().astype(str)
        known_hs10.update(code for code in codes if len(code) == 10)

    frames: list[pd.DataFrame] = []
    output_paths: list[Path] = []
    for item_path in item_paths:
        year = item_path.parent.name.split("=", 1)[1]
        summary_path = summary_root / f"year={year}" / f"{year}01_{year}12.parquet"
        if not summary_path.exists() and year == "2026":
            summary_path = summary_root / "year=2026" / "202601_202608.parquet"
        if not summary_path.exists():
            raise RuntimeError(f"{year}년 수출입총괄 Parquet가 없습니다.")
        result = aggregate_hs6_year(
            pd.read_parquet(item_path),
            pd.read_parquet(summary_path),
            known_hs10=known_hs10,
        )
        year_dir = output_root / f"year={year}"
        year_dir.mkdir(parents=True, exist_ok=True)
        output_path = year_dir / f"hs6_{year}.parquet"
        result.to_parquet(output_path, index=False, compression="zstd")
        output_paths.append(output_path)
        frames.append(result)

    combined = pd.concat(frames, ignore_index=True)
    start_month = str(combined["date"].min())
    end_month = str(combined["date"].max())
    csv_path = output_root / f"hs6_{start_month.replace('-', '')}_{end_month.replace('-', '')}.csv.gz"
    combined.to_csv(csv_path, index=False, encoding="utf-8-sig", compression="gzip")
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "start_month": start_month,
        "end_month": end_month,
        "rows": len(combined),
        "months": int(combined["date"].nunique()),
        "mapped_hs6_codes": int(
            combined.loc[combined["data_status"] == "mapped", "hs6_code"].nunique()
        ),
        "unmapped_rows": int((combined["data_status"] == "unmapped").sum()),
        "reconciliation_rows": int(
            (combined["data_status"] == "reconciliation_adjustment").sum()
        ),
        "parquet_files": len(output_paths),
        "csv_path": str(csv_path),
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def _unit_price(value: pd.Series, weight: pd.Series) -> pd.Series:
    price = pd.Series(pd.NA, index=value.index, dtype="Float64")
    valid = weight.notna() & (weight > 0)
    price.loc[valid] = value.loc[valid] / weight.loc[valid]
    return price
