"""Versioned contract and validation for the self-contained web dashboard JSON."""

from __future__ import annotations

import calendar
import hashlib
import json
import math
import re
from collections import defaultdict
from pathlib import Path

from catalog_mapping import validate as validate_catalog
from industry_rules import digest as industry_digest, expand as expand_industries, item_industries, validate_rules


SCHEMA_VERSION = 4
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
QUARTER = re.compile(r"^\d{4}-Q[1-4]$")
CODE = re.compile(r"^[A-Z]{2}$")
COLUMNS = {
    "summary": ["month", "export_usd", "export_yoy_fraction", "export_mom_fraction", "avg_daily_export_usd", "import_usd", "trade_balance_usd"],
    "industries": ["month", "industry", "export_usd", "yoy_fraction", "mom_fraction"],
    "hsIndustries": ["month", "hs_reference_group", "export_usd"],
    "subindustries": ["month", "industry", "hs4", "export_usd"],
    "products": ["month", "export_usd", "export_weight_kg", "import_usd", "export_unit_usd_per_kg", "export_mom_fraction", "export_yoy_fraction", "unit_yoy_fraction"],
    "productQuarterly": ["quarter", "export_usd", "export_weight_kg", "import_usd", "export_unit_usd_per_kg", "export_qoq_fraction", "export_yoy_fraction", "unit_yoy_fraction"],
    "countryItems": ["month", "country_code", "export_usd"],
    "regions": ["month", "mapped_region", "api_locality", "export_usd"],
}


class SnapshotError(ValueError):
    pass


def catalog_digest(catalog: list[dict]) -> str:
    canonical = json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def previous_month(month: str, count: int) -> str:
    year, number = map(int, month.split("-"))
    index = year * 12 + number - 1 - count
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def growth(value: int | float | None, previous: int | float | None) -> float | None:
    return value / previous - 1 if value is not None and previous else None


def _unit(value: int | float, weight: int | float) -> float | None:
    return value / weight if weight > 0 else None


def enrich_products(raw: dict[str, list[list]]) -> tuple[dict[str, list[list]], dict[str, list[list]]]:
    """Calculate monthly and complete-quarter growth once, during publication."""
    monthly: dict[str, list[list]] = {}
    quarterly: dict[str, list[list]] = {}
    for key, source in raw.items():
        by_month = {row[0]: row for row in source}
        if len(by_month) != len(source):
            raise SnapshotError(f"{key}: 중복 월이 있습니다.")
        months = []
        for date, value, weight, imports in sorted(source):
            prior = by_month.get(previous_month(date, 1))
            year = by_month.get(previous_month(date, 12))
            unit = _unit(value, weight)
            year_unit = _unit(year[1], year[2]) if year else None
            months.append([date, value, weight, imports, unit,
                           growth(value, prior[1]) if prior else None,
                           growth(value, year[1]) if year else None,
                           growth(unit, year_unit)])
        monthly[key] = months
        buckets: dict[str, list[list]] = defaultdict(list)
        for row in months:
            year, month = map(int, row[0].split("-"))
            buckets[f"{year:04d}-Q{(month - 1) // 3 + 1}"].append(row)
        complete = []
        for quarter, part in sorted(buckets.items()):
            if len(part) != 3:
                continue
            value = sum(row[1] for row in part)
            weight = sum(row[2] for row in part)
            imports = sum(row[3] for row in part)
            complete.append([quarter, value, weight, imports, _unit(value, weight)])
        lookup = {row[0]: row for row in complete}
        qrows = []
        for quarter, value, weight, imports, unit in complete:
            year, q = int(quarter[:4]), int(quarter[-1])
            prior_quarter = f"{year - 1:04d}-Q4" if q == 1 else f"{year:04d}-Q{q - 1}"
            prior_year = f"{year - 1:04d}-Q{q}"
            prior = lookup.get(prior_quarter)
            year_row = lookup.get(prior_year)
            qrows.append([quarter, value, weight, imports, unit,
                          growth(value, prior[1]) if prior else None,
                          growth(value, year_row[1]) if year_row else None,
                          growth(unit, year_row[4]) if year_row else None])
        quarterly[key] = qrows
    return monthly, quarterly


def _fail(message: str) -> None:
    raise SnapshotError(message)


def _number(value: object, label: str, *, nullable: bool = False, nonnegative: bool = False) -> None:
    if value is None and nullable:
        return
    if type(value) not in (int, float) or not math.isfinite(value):
        _fail(f"{label}: 유한한 숫자여야 합니다.")
    if nonnegative and value < 0:
        _fail(f"{label}: 음수일 수 없습니다.")


def _near(actual: object, expected: object, label: str) -> None:
    if actual is None or expected is None:
        if actual is not expected:
            _fail(f"{label}: 계산된 값과 다릅니다 ({actual} != {expected}).")
        return
    _number(actual, label)
    if not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-8):
        _fail(f"{label}: 계산된 값과 다릅니다 ({actual} != {expected}).")


def _rows(data: object, label: str, width: int) -> list[list]:
    if not isinstance(data, list):
        _fail(f"{label}: 배열이어야 합니다.")
    for index, row in enumerate(data):
        if not isinstance(row, list) or len(row) != width:
            _fail(f"{label}[{index}]: {width}개 필드가 필요합니다.")
    return data


def validate_snapshot(snapshot: object, *, source_catalog: list[dict] | None = None, source_industry_rules: dict | None = None) -> dict:
    """Fail before publication if structure, mapping links, or metrics are inconsistent."""
    if not isinstance(snapshot, dict) or snapshot.get("schemaVersion") != SCHEMA_VERSION:
        _fail(f"웹 데이터 형식은 schemaVersion={SCHEMA_VERSION}이어야 합니다.")
    if snapshot.get("columns") != COLUMNS:
        _fail("필드 정의가 현재 웹 데이터 계약과 다릅니다.")
    catalog = validate_catalog(snapshot.get("catalog"))
    if snapshot.get("catalogDigest") != catalog_digest(catalog):
        _fail("품목 매핑 해시가 일치하지 않습니다.")
    if source_catalog is not None and catalog_digest(validate_catalog(source_catalog)) != snapshot["catalogDigest"]:
        _fail("매핑 파일이 웹 데이터 생성 이후 변경되었습니다. 데이터를 다시 생성하세요.")
    active = {f"{row['l']}-{row['c']}" for row in catalog if row["enabled"]}
    rules = validate_rules(snapshot.get("industryRules"))
    if snapshot.get("industryRulesDigest") != industry_digest(rules):
        _fail("산업 규칙 해시가 일치하지 않습니다.")
    if source_industry_rules is not None and industry_digest(source_industry_rules) != snapshot["industryRulesDigest"]:
        _fail("산업 규칙 파일이 웹 데이터 생성 이후 변경되었습니다. 데이터를 다시 생성하세요.")
    hs6_map = snapshot.get("industryByHS6")
    if not isinstance(hs6_map, dict) or not hs6_map or any(not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code) for code in hs6_map):
        _fail("industryByHS6 코드 형식이 잘못되었습니다.")
    if hs6_map != expand_industries(rules, set(hs6_map)):
        _fail("HS6 산업 배정이 산업 규칙과 다릅니다.")
    item_industry = snapshot.get("itemIndustry")
    if item_industry != item_industries(catalog, hs6_map) or set(item_industry) != active:
        _fail("대표 품목의 산업 배정이 HS6 산업 규칙과 다릅니다.")
    as_of = snapshot.get("asOf")
    if not isinstance(as_of, str) or not MONTH.fullmatch(as_of):
        _fail("asOf는 YYYY-MM이어야 합니다.")
    if snapshot.get("status") != "final":
        _fail("현재 웹 데이터는 확정 월간 데이터만 허용합니다.")

    summary = _rows(snapshot.get("summary"), "summary", 7)
    if not summary:
        _fail("summary가 비었습니다.")
    previous = ""
    summary_lookup = {}
    for row in summary:
        date, export, yoy, mom, daily, imports, balance = row
        if not isinstance(date, str) or not MONTH.fullmatch(date) or date <= previous:
            _fail(f"summary: 월 형식·정렬·중복 오류 ({date}).")
        if previous and previous_month(date, 1) != previous:
            _fail(f"summary: {previous} 다음 월이 {date}입니다.")
        previous = date
        for label, value in (("export", export), ("daily", daily), ("import", imports)):
            _number(value, f"summary {date} {label}", nonnegative=True)
        for label, value in (("yoy", yoy), ("mom", mom)):
            _number(value, f"summary {date} {label}", nullable=True)
        _number(balance, f"summary {date} balance")
        _near(balance, export - imports, f"summary {date} 무역수지")
        days = calendar.monthrange(int(date[:4]), int(date[5:]))[1]
        _near(daily, export / days, f"summary {date} 일평균")
        prior = summary_lookup.get(previous_month(date, 1))
        year = summary_lookup.get(previous_month(date, 12))
        _near(mom, growth(export, prior[1]) if prior else None, f"summary {date} MoM")
        _near(yoy, growth(export, year[1]) if year else None, f"summary {date} YoY")
        summary_lookup[date] = row
    if summary[-1][0] != as_of:
        _fail("asOf와 총수출 최신월이 다릅니다.")

    names = snapshot.get("industryNames")
    if not isinstance(names, list) or not names or any(not isinstance(n, str) or not n for n in names) or len(set(names)) != len(names):
        _fail("industryNames가 비었거나 중복되었습니다.")
    industry_as_of = snapshot.get("industryAsOf")
    coverage_start = snapshot.get("industryCoverageStart")
    coverage_end = snapshot.get("industryCoverageEnd")
    common_as_of = snapshot.get("industryCommonAsOf")
    if any(not isinstance(value, str) or not MONTH.fullmatch(value) for value in (industry_as_of, coverage_start, coverage_end, common_as_of)):
        _fail("산업 기준월·수록 범위 형식이 잘못되었습니다.")
    if coverage_start > coverage_end or industry_as_of != coverage_end or common_as_of != min(as_of, industry_as_of):
        _fail("산업 기준월·수록 범위·공통 기준월이 서로 맞지 않습니다.")
    for field in ("industryMethod", "industryClassification", "industryPrecedenceRule"):
        if not isinstance(snapshot.get(field), str) or not snapshot[field]:
            _fail(f"{field}가 비었습니다.")
    industries = _rows(snapshot.get("industries"), "industries", 5)
    industry_values = {}
    industry_previous = {}
    for date, name, value, yoy, mom in industries:
        if not isinstance(date, str) or not MONTH.fullmatch(date) or date > industry_as_of or name not in names:
            _fail(f"industries: 잘못된 월/산업 ({date}, {name}).")
        _number(value, f"industries {date}/{name}", nonnegative=True)
        _number(yoy, f"industries {date}/{name} YoY", nullable=True)
        _number(mom, f"industries {date}/{name} MoM", nullable=True)
        if (date, name) in industry_values:
            _fail(f"industries: 중복 {date}/{name}.")
        prior = industry_previous.get(name)
        expected_mom = growth(value, prior[1]) if prior and previous_month(date, 1) == prior[0] else None
        if expected_mom is None:
            if mom is not None:
                _fail(f"industries {date}/{name} MoM: 첫 수록월은 null이어야 합니다.")
        elif mom is None or not math.isclose(mom, expected_mom, abs_tol=0.0002):
            _fail(f"industries {date}/{name} MoM: 원자료 반올림 허용범위를 벗어났습니다.")
        industry_values[date, name] = value
        industry_previous[name] = (date, value)
    dates = sorted({date for date, _ in industry_values})
    if not industry_values or dates[0] != coverage_start or dates[-1] != coverage_end:
        _fail("산업별 데이터의 수록 범위가 메타데이터와 다릅니다.")
    if any({name for date, name in industry_values if date == month} != set(names) for month in dates):
        _fail("산업별 데이터는 매월 모든 산업을 포함해야 합니다.")
    if snapshot.get("industrySourceRows") != len(industries):
        _fail("industrySourceRows가 산업 행 수와 다릅니다.")
    if snapshot.get("industryClassification") == "MOTIR_20_MAIN_EXPORTS_2026":
        if len(names) != 20 or len(industries) != 340 or coverage_start != "2025-05" or coverage_end != "2026-09":
            _fail("공식 MTI 산업 데이터는 20개 산업·340행·2025-05~2026-09이어야 합니다.")

    hs_names = snapshot.get("hsIndustryNames")
    if hs_names != rules["industryNames"]:
        _fail("hsIndustryNames가 HS 산업 규칙과 다릅니다.")
    hs_industries = _rows(snapshot.get("hsIndustries"), "hsIndustries", 3)
    hs_values = {}
    for date, name, value in hs_industries:
        if not isinstance(date, str) or not MONTH.fullmatch(date) or date > as_of or name not in hs_names:
            _fail(f"hsIndustries: 잘못된 월/HS 참고 그룹 ({date}, {name}).")
        _number(value, f"hsIndustries {date}/{name}", nonnegative=True)
        if (date, name) in hs_values:
            _fail(f"hsIndustries: 중복 {date}/{name}.")
        hs_values[date, name] = value
    if not hs_values or max(date for date, _ in hs_values) != as_of:
        _fail("HS 참고 그룹 데이터의 최신월이 총수출과 다릅니다.")

    bridges = snapshot.get("industryHsGroups")
    if not isinstance(bridges, dict) or set(bridges) != set(names) or any(
        not isinstance(groups, list) or any(group not in hs_names for group in groups) for groups in bridges.values()
    ):
        _fail("공식 산업과 HS 참고 그룹 연결이 잘못되었습니다.")
    catalog_groups = snapshot.get("industryCatalogGroups")
    catalog_industries = {row["i"] for row in catalog}
    if not isinstance(catalog_groups, dict) or set(catalog_groups) != set(names) or any(
        not isinstance(groups, list) or any(group not in catalog_industries for group in groups) for groups in catalog_groups.values()
    ):
        _fail("공식 산업과 대표 품목 연결이 잘못되었습니다.")
    quality = snapshot.get("industryQuality")
    if not isinstance(quality, dict) or not isinstance(quality.get("reconciliation"), list):
        _fail("산업 데이터 품질 메타데이터가 잘못되었습니다.")

    sub = _rows(snapshot.get("subindustries"), "subindustries", 4)
    sub_values = defaultdict(int)
    sub_keys = set()
    for date, name, hs4, value in sub:
        if not isinstance(date, str) or not MONTH.fullmatch(date) or (date, name) not in hs_values or not isinstance(hs4, str) or not re.fullmatch(r"[0-9]{4}", hs4):
            _fail(f"subindustries: 잘못된 월/산업/HS4 ({date}, {name}, {hs4}).")
        _number(value, f"subindustries {date}/{name}/{hs4}", nonnegative=True)
        if (date, name, hs4) in sub_keys:
            _fail(f"subindustries: 중복 {date}/{name}/{hs4}.")
        sub_keys.add((date, name, hs4))
        sub_values[date, name] += value
    for pair, value in sub_values.items():
        _near(value, hs_values[pair], f"세부산업 합계 {pair}")
    if not sub_values or max(date for date, _ in sub_values) != as_of:
        _fail("세부산업 최신월이 총수출과 다릅니다.")

    products = snapshot.get("products")
    quarters = snapshot.get("productQuarterly")
    countries = snapshot.get("countryItems")
    for label, collection in (("products", products), ("productQuarterly", quarters), ("countryItems", countries)):
        if not isinstance(collection, dict) or set(collection) != active:
            _fail(f"{label}: 활성 매핑 HS코드와 키가 일치하지 않습니다.")
    product_lookup = {}
    for key in sorted(active):
        monthly = _rows(products[key], f"products {key}", 8)
        if not monthly:
            _fail(f"products {key}: 시계열이 비었습니다.")
        by_month = {}
        for date, value, weight, imports, unit, mom, yoy, unit_yoy in monthly:
            if not isinstance(date, str) or not MONTH.fullmatch(date) or date > as_of or (by_month and date <= next(reversed(by_month))):
                _fail(f"products {key}: 월 정렬·형식 오류 {date}.")
            for label, amount in (("value", value), ("weight", weight), ("imports", imports)):
                _number(amount, f"products {key}/{date} {label}", nonnegative=True)
            _near(unit, _unit(value, weight), f"products {key}/{date} 단가")
            prior = by_month.get(previous_month(date, 1))
            year = by_month.get(previous_month(date, 12))
            _near(mom, growth(value, prior[1]) if prior else None, f"products {key}/{date} MoM")
            _near(yoy, growth(value, year[1]) if year else None, f"products {key}/{date} YoY")
            _near(unit_yoy, growth(unit, year[4]) if year else None, f"products {key}/{date} 단가 YoY")
            by_month[date] = [date, value, weight, imports, unit]
        product_lookup[key] = by_month
        expected_quarters = {}
        grouped = defaultdict(list)
        for row in monthly:
            date = row[0]
            grouped[f"{date[:4]}-Q{(int(date[5:]) - 1) // 3 + 1}"].append(row)
        for date, rows in grouped.items():
            if len(rows) == 3:
                expected_quarters[date] = [sum(row[i] for row in rows) for i in (1, 2, 3)]
        actual_quarters = _rows(quarters[key], f"productQuarterly {key}", 8)
        if {row[0] for row in actual_quarters} != set(expected_quarters):
            _fail(f"productQuarterly {key}: 완료된 분기 목록이 월별 데이터와 다릅니다.")
        quarter_lookup = {}
        for row in actual_quarters:
            date, value, weight, imports, unit, qoq, yoy, unit_yoy = row
            if not isinstance(date, str) or not QUARTER.fullmatch(date) or (quarter_lookup and date <= next(reversed(quarter_lookup))):
                _fail(f"productQuarterly {key}: 분기 형식·정렬 오류 {date}.")
            for actual, expected, label in zip((value, weight, imports), expected_quarters[date], ("수출액", "중량", "수입액")):
                _near(actual, expected, f"productQuarterly {key}/{date} {label}")
            _near(unit, _unit(value, weight), f"productQuarterly {key}/{date} 단가")
            year, q = int(date[:4]), int(date[-1])
            prior = quarter_lookup.get(f"{year - 1:04d}-Q4" if q == 1 else f"{year:04d}-Q{q - 1}")
            year_row = quarter_lookup.get(f"{year - 1:04d}-Q{q}")
            _near(qoq, growth(value, prior[1]) if prior else None, f"productQuarterly {key}/{date} QoQ")
            _near(yoy, growth(value, year_row[1]) if year_row else None, f"productQuarterly {key}/{date} YoY")
            _near(unit_yoy, growth(unit, year_row[4]) if year_row else None, f"productQuarterly {key}/{date} 단가 YoY")
            quarter_lookup[date] = row

    country_names = snapshot.get("countryNames")
    if not isinstance(country_names, dict) or any(not isinstance(name, str) or not name for name in country_names.values()):
        _fail("countryNames가 객체가 아닙니다.")
    for key in sorted(active):
        rows = _rows(countries[key], f"countryItems {key}", 3)
        if not rows:
            _fail(f"countryItems {key}: 국가별 시계열이 비었습니다.")
        seen = set()
        totals = defaultdict(int)
        previous_pair = ("", "")
        for date, code, value in rows:
            if not isinstance(date, str) or not MONTH.fullmatch(date) or date > as_of or not isinstance(code, str) or not CODE.fullmatch(code) or code not in country_names:
                _fail(f"countryItems {key}: 잘못된 월/국가 {date}/{code}.")
            _number(value, f"countryItems {key}/{date}/{code}", nonnegative=True)
            if (date, code) in seen:
                _fail(f"countryItems {key}: 중복 {date}/{code}.")
            if (date, code) <= previous_pair:
                _fail(f"countryItems {key}: 월·국가 정렬 오류 {date}/{code}.")
            previous_pair = (date, code)
            seen.add((date, code))
            totals[date] += value
        for date, value in totals.items():
            product = product_lookup[key].get(date)
            if not product or value > product[1] + max(1000, product[1] * 0.001):
                _fail(f"countryItems {key}/{date}: 국가 합계가 품목 수출액과 일치하지 않습니다.")

    regions = snapshot.get("regions")
    if not isinstance(regions, dict):
        _fail("regions가 객체가 아닙니다.")
    for code, rows in regions.items():
        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
            _fail(f"regions: 잘못된 HS6 코드 {code}.")
        previous_region = ("", "", "")
        for date, mapped, locality, value in _rows(rows, f"regions {code}", 4):
            if not isinstance(date, str) or not MONTH.fullmatch(date) or date > as_of or not isinstance(mapped, str) or not mapped or not isinstance(locality, str) or not locality:
                _fail(f"regions {code}: 잘못된 월/지역입니다.")
            if (date, mapped, locality) <= previous_region:
                _fail(f"regions {code}: 중복 또는 정렬 오류 {date}/{mapped}/{locality}.")
            previous_region = (date, mapped, locality)
            _number(value, f"regions {code}/{date}/{mapped}", nonnegative=True)
    return {"schemaVersion": SCHEMA_VERSION, "asOf": as_of, "catalogItems": len(catalog), "activeItems": len(active), "countryRows": sum(map(len, countries.values()))}


def validate_file(path: Path, *, source_catalog: list[dict] | None = None, source_industry_rules: dict | None = None) -> dict:
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SnapshotError(f"웹 데이터 JSON 구문 오류: {exc}") from exc
    return validate_snapshot(snapshot, source_catalog=source_catalog, source_industry_rules=source_industry_rules)
