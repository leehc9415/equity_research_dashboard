"""Build a portable, real-data snapshot for the local export dashboard.

The investor-facing item mapping is intentionally separate from the trade series.
The editable catalog is validated before any snapshot is written.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from catalog_mapping import CATALOG, load
from industry_rules import digest as industry_digest, expand as expand_industries, item_industries, load_rules
from motir20 import load_official_series
from web_snapshot import COLUMNS, SCHEMA_VERSION, catalog_digest, enrich_products, validate_snapshot


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "local" / "analytics"
OUT = ROOT / "dashboard" / "data"
REGION_DATA = ROOT / "data" / "processed" / "region-series.json"

def records(frame: pd.DataFrame, columns: list[str]) -> list[list]:
    clean = frame[columns].astype(object).where(pd.notna(frame[columns]), None)
    return clean.values.tolist()


def load_catalog() -> list[dict]:
    return load(CATALOG)


def load_region_data(path: Path = REGION_DATA) -> dict[str, list[list]]:
    if not path.exists():
        raise FileNotFoundError(f"지역 시계열 입력이 없습니다: {path}. import_region_data.py로 먼저 생성하세요.")
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schemaVersion") != 1 or not isinstance(document.get("regions"), dict) or not document["regions"]:
        raise ValueError(f"지역 시계열 입력 형식이 잘못되었습니다: {path}")
    return document["regions"]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    catalog = load_catalog()
    active_catalog = [item for item in catalog if item["enabled"]]
    keys = [(item["l"], item["c"]) for item in active_catalog]
    summary = pd.read_parquet(BASE / "trade-summary-timeseries/monthly.parquet")
    summary = summary.sort_values("date")
    latest = str(summary.date.iloc[-1])
    summary_rows = records(summary, ["date", "export_value_usd", "export_value_yoy_pct", "export_value_mom_pct", "avg_daily_export_usd", "import_value_usd", "trade_balance_usd"])

    hs6 = pd.read_parquet(
        BASE / "item-timeseries/hs6_monthly.parquet",
        columns=["date", "hs_code", "export_value_usd", "export_weight_kg", "import_value_usd"],
    )
    hs6 = hs6[hs6.date >= "2009-01"].copy()
    industry_rules = load_rules()
    code_industry = expand_industries(industry_rules, set(hs6.hs_code.unique()))
    hs6["industry"] = hs6.hs_code.map(code_industry)
    hs_industries = hs6.groupby(["date", "industry"], as_index=False)["export_value_usd"].sum()
    hs_industries = hs_industries[hs_industries.date >= "2010-01"].sort_values(["date", "industry"])
    hs_industry_rows = records(hs_industries, ["date", "industry", "export_value_usd"])
    hs6["hs4"] = hs6.hs_code.str[:4]
    subindustry_start = str((pd.Period(latest, freq="M") - 13).strftime("%Y-%m"))
    subindustries = hs6[hs6.date >= subindustry_start].groupby(
        ["date", "industry", "hs4"], as_index=False
    )["export_value_usd"].sum()
    subindustry_rows = records(
        subindustries.sort_values(["date", "industry", "hs4"]),
        ["date", "industry", "hs4", "export_value_usd"],
    )

    # Product series use the exact HS tier represented by each mapping row.
    hs6_codes = {code for level, code in keys if level == "HS6"}
    hs4_codes = {code for level, code in keys if level == "HS4"}
    hs10_codes = {code for level, code in keys if level == "HS10"}
    hs10 = pd.read_parquet(
        BASE / "item-timeseries/hs10_monthly.parquet",
        columns=["date", "hs_code", "export_value_usd", "export_weight_kg", "import_value_usd"],
        filters=[("date", ">=", "2009-01")],
    )
    hs10 = hs10[hs10.hs_code.isin(hs10_codes)].copy()
    hs6_product = hs6[hs6.hs_code.isin(hs6_codes)].copy()
    hs4_product = hs6[hs6.hs_code.str[:4].isin(hs4_codes)].copy()
    hs4_product["hs_code"] = hs4_product.hs_code.str[:4]
    hs4_product = hs4_product.groupby(["date", "hs_code"], as_index=False)[["export_value_usd", "export_weight_kg", "import_value_usd"]].sum()
    products: dict[str, list[list]] = {}
    cols = ["date", "export_value_usd", "export_weight_kg", "import_value_usd"]
    for level, frame in [("HS4", hs4_product), ("HS6", hs6_product), ("HS10", hs10)]:
        for code, group in frame.groupby("hs_code"):
            group = group.sort_values("date")
            products[f"{level}-{code}"] = records(group, cols)
    missing = sorted(f"{level}-{code}" for level, code in keys if f"{level}-{code}" not in products)
    if missing:
        raise ValueError("매핑된 HS코드의 원본 시계열이 없습니다. 기존 대시보드 파일은 유지됩니다: " + ", ".join(missing[:20]))

    # Country detail is materialized for each mapped item, covering the latest
    # 32 months (with a prior-year comparator for the latest-month rankings).
    country_frames = []
    chapters = sorted({code[:2] for _, code in keys})
    latest_year = int(latest[:4])
    for year in range(latest_year - 3, latest_year + 1):
        for chapter in chapters:
            path = BASE / f"item-country-timeseries/hs10-monthly/year={year}/chapter={chapter}/data.parquet"
            if not path.exists():
                continue
            part = pd.read_parquet(path, columns=["date", "hs_code", "country_code", "export_value_usd"])
            part = part[part.date >= f"{latest_year - 3}-01"]
            wanted = [code for _, code in keys if code.startswith(chapter)]
            matched = pd.Series(False, index=part.index)
            for code in wanted:
                matched |= part.hs_code.str.startswith(code)
            part = part[matched]
            if not part.empty:
                country_frames.append(part)
    country_items: dict[str, list[list]] = {}
    if country_frames:
        country = pd.concat(country_frames, ignore_index=True)
        for level, length in [("HS4", 4), ("HS6", 6), ("HS10", 10)]:
            available = {code for lv, code in keys if lv == level}
            subset = country[country.hs_code.str[:length].isin(available)].copy()
            subset["item_code"] = subset.hs_code.str[:length]
            grouped = subset.groupby(["item_code", "date", "country_code"], as_index=False)["export_value_usd"].sum()
            for code, group in grouped.groupby("item_code"):
                country_items[f"{level}-{code}"] = records(group.sort_values(["date", "country_code"]), ["date", "country_code", "export_value_usd"])

    countries = pd.read_parquet(BASE / "country-timeseries/monthly.parquet", columns=["country_code", "country_name", "date", "export_value_usd"])
    countries = countries.sort_values("date").drop_duplicates("country_code", keep="last")
    country_names = {row.country_code: row.country_name for row in countries.itertuples() if isinstance(row.country_code, str)}
    country_names.update({"CN": "중국", "US": "미국", "TW": "대만", "VN": "베트남", "JP": "일본", "HK": "홍콩", "SG": "싱가포르", "PH": "필리핀", "IN": "인도", "DE": "독일", "MX": "멕시코", "TH": "태국", "MY": "말레이시아", "ID": "인도네시아"})

    regions = load_region_data()

    product_monthly, product_quarterly = enrich_products(products)
    official = load_official_series()
    mapped_industries = item_industries(catalog, code_industry)
    missing_industries = [key for key, value in mapped_industries.items() if value is None and not key.startswith("HS4-")]
    if missing_industries:
        raise ValueError("품목의 HS6 산업 배정이 없습니다: " + ", ".join(missing_industries[:20]))
    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "columns": COLUMNS,
        "catalog": catalog,
        "catalogDigest": catalog_digest(catalog),
        "industryRules": industry_rules,
        "industryRulesDigest": industry_digest(industry_rules),
        "industryByHS6": code_industry,
        "itemIndustry": mapped_industries,
        "asOf": latest,
        "status": str(summary.period_status.iloc[-1]),
        "updatedAt": json.loads((BASE / "last-update.json").read_text(encoding="utf-8")).get("generated_at") if (BASE / "last-update.json").exists() else None,
        "snapshotBuiltAt": datetime.now(timezone.utc).isoformat(),
        "industryMethod": official["method"],
        "industryClassification": official["classification"],
        "industryPrecedenceRule": official["precedenceRule"],
        "industryAsOf": official["coverageEnd"],
        "industryCommonAsOf": min(latest, official["coverageEnd"]),
        "industryCoverageStart": official["coverageStart"],
        "industryCoverageEnd": official["coverageEnd"],
        "industrySourceRows": official["rowCount"],
        "industryHsGroups": official["hsReferenceGroups"],
        "industryCatalogGroups": official["catalogGroups"],
        "industryQuality": official["quality"],
        "summary": summary_rows,
        "industries": official["rows"],
        "hsIndustries": hs_industry_rows,
        "subindustries": subindustry_rows,
        "industryNames": official["names"],
        "hsIndustryNames": industry_rules["industryNames"],
        "products": product_monthly,
        "productQuarterly": product_quarterly,
        "countryItems": country_items,
        "countryNames": country_names,
        "regions": regions,
    }
    validation = validate_snapshot(payload, source_catalog=catalog, source_industry_rules=industry_rules)
    target = OUT / "dashboard-data.json"
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Wrote {target} ({target.stat().st_size / 1024 / 1024:.1f} MiB)")
    print(f"Validated schema v{validation['schemaVersion']}; catalog {len(catalog)} items; {len(products)} product series; {len(country_items)} country series; latest {latest}")


if __name__ == "__main__":
    main()
