"""Update the portable dashboard bundle after the official final release.

This path is designed for a GitHub-hosted runner. It needs only the committed
dashboard JSON, editable mapping files and the API key; local Parquet is not
required. A missing release or API month never changes the published bundle.
"""

from __future__ import annotations

import argparse
import calendar
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

from catalog_mapping import CATALOG, load as load_catalog
from industry_rules import RULES_PATH, digest as industry_digest, expand as expand_industries, item_industries, load_rules
from web_snapshot import SnapshotError, catalog_digest, enrich_products, growth, previous_month, validate_snapshot
from investment_data.api_catalog import ENDPOINTS
from investment_data.client import KcsClient, load_api_key
from investment_data.parser import parse_xml


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "dashboard" / "data" / "dashboard-data.json"
NOTICE_URL = "https://www.customs.go.kr/kcs/na/ntt/selectNttList.do"
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
HS10_RE = re.compile(r"^\d{10}$")


class _NoticeLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        fields = dict(attrs)
        if "nttInfoBtn" in (fields.get("class") or "").split():
            self.links.append({key: value or "" for key, value in fields.items()})


def next_month(month: str) -> str:
    return previous_month(month, -1)


def final_release_url(month: str, *, session: requests.Session | None = None) -> str | None:
    """Require the exact monthly-final announcement, not an API-only month."""
    year, number = map(int, month.split("-"))
    title = f"{year}년 {number}월 월간 수출입 현황 [확정치]"
    http = session or requests.Session()
    response = http.get(
        NOTICE_URL,
        params={
            "bbsId": "1362", "mi": "2891", "searchType": "sj",
            "searchValue": f"{year}년 {number}월 월간 수출입 현황",
            "currPage": "1", "listCo": "10",
        },
        timeout=30,
    )
    response.raise_for_status()
    links = _NoticeLinks()
    links.feed(response.text)
    for link in links.links:
        if re.sub(r"\s+", " ", link.get("title", "")).strip() != title:
            continue
        notice_id = link.get("data-id", "")
        notice_token = link.get("data-url", "")
        if re.fullmatch(r"\d+", notice_id) and re.fullmatch(r"[0-9a-f]+", notice_token):
            return (
                "https://www.customs.go.kr/kcs/na/ntt/selectNttInfo.do"
                f"?bbsId=1362&mi=2891&nttSn={notice_id}&nttSnUrl={notice_token}"
            )
    return None


def _monthly(frame: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    if frame.empty or "date" not in frame:
        return pd.DataFrame()
    dates = frame["date"].astype(str)
    return frame.loc[dates.str.fullmatch(MONTH_RE.pattern) & dates.between(start, end)].copy()


def _api_frame(client: KcsClient, slug: str, start: str, end: str, **extra: str) -> pd.DataFrame:
    endpoint = ENDPOINTS[slug]
    payload = client.fetch(
        endpoint,
        {"strtYymm": start.replace("-", ""), "endYymm": end.replace("-", ""), **extra},
    )
    return _monthly(parse_xml(payload, endpoint).frame, start, end)


def _periods(target: str) -> list[tuple[str, str]]:
    periods = [(f"{target[:4]}-01", target)]
    # The previous year's annual figures can be revised in February. Refresh
    # that entire year with the February monthly release (normally in March).
    if target.endswith("-02"):
        year = int(target[:4]) - 1
        periods.insert(0, (f"{year}-01", f"{year}-12"))
    return periods


def _sum_row(frame: pd.DataFrame, key: str, date: str) -> list:
    row = frame.loc[frame["date"].astype(str).eq(date)]
    if len(row) != 1:
        raise RuntimeError(f"{key} {date}: API 월별 행이 정확히 1개여야 합니다.")
    item = row.iloc[0]
    return [date, int(item.export_value_usd), int(item.import_value_usd)]


def _metadata_sync(snapshot: dict, catalog: list[dict], rules: dict) -> bool:
    if snapshot["industryRulesDigest"] != industry_digest(rules):
        raise RuntimeError("산업 규칙 변경은 과거 전체 산업 합계 재계산이 필요합니다. 로컬에서 build_dashboard_data.py를 실행하고 결과를 GitHub에 올리세요.")
    if snapshot["catalogDigest"] != catalog_digest(catalog):
        old_keys = {f"{row['l']}-{row['c']}" for row in snapshot["catalog"] if row["enabled"]}
        new_keys = {f"{row['l']}-{row['c']}" for row in catalog if row["enabled"]}
        if old_keys != new_keys:
            raise RuntimeError("품목 HS코드·활성 목록 변경은 과거 시계열 재생성이 필요합니다. 로컬에서 build_dashboard_data.py를 실행하고 결과를 GitHub에 올리세요.")
        snapshot["catalog"] = catalog
        snapshot["catalogDigest"] = catalog_digest(catalog)
        snapshot["itemIndustry"] = item_industries(catalog, snapshot["industryByHS6"])
        return True
    return False


def _item_aggregates(items: pd.DataFrame, snapshot: dict) -> tuple[dict, dict, dict]:
    if items.empty or not {"hs_code", "date", "export_value_usd", "export_weight_kg", "import_value_usd"} <= set(items):
        raise RuntimeError("품목 API 월별 데이터가 비었거나 필수 필드가 없습니다.")
    items = items[items.hs_code.astype(str).str.fullmatch(HS10_RE.pattern)].copy()
    if items.duplicated(["date", "hs_code"]).any():
        raise RuntimeError("품목 API에 월·HS10 중복이 있습니다.")
    items["hs6"] = items.hs_code.astype(str).str[:6]
    items["hs4"] = items.hs_code.astype(str).str[:4]
    hs6_map = expand_industries(snapshot["industryRules"], set(snapshot["industryByHS6"]) | set(items.hs6))
    items["industry"] = items.hs6.map(hs6_map)
    industry = items.groupby(["date", "industry"], as_index=False)["export_value_usd"].sum()
    sub = items.groupby(["date", "industry", "hs4"], as_index=False)["export_value_usd"].sum()
    wanted = set(snapshot["products"])
    product: dict[str, list[list]] = defaultdict(list)
    for level, column in (("HS4", "hs4"), ("HS6", "hs6"), ("HS10", "hs_code")):
        grouped = items.groupby(["date", column], as_index=False)[
            ["export_value_usd", "export_weight_kg", "import_value_usd"]
        ].sum()
        for row in grouped.itertuples(index=False, name=None):
            date, code, value, weight, imports = row
            key = f"{level}-{code}"
            if key in wanted:
                product[key].append([str(date), int(value), int(weight), int(imports)])
    return (
        {"map": hs6_map, "rows": [[str(d), str(n), int(v)] for d, n, v in industry.itertuples(index=False, name=None)]},
        {"rows": [[str(d), str(n), str(h), int(v)] for d, n, h, v in sub.itertuples(index=False, name=None)]},
        product,
    )


def _country_item_rows(frame: pd.DataFrame, code: str, wanted: dict[str, set[str]]) -> list[tuple[str, str, str, int]]:
    if frame.empty or "hs_code" not in frame:
        return []
    standard = frame[frame.hs_code.astype(str).str.fullmatch(HS10_RE.pattern)].copy()
    if standard.empty:
        return []
    standard["hs4"] = standard.hs_code.astype(str).str[:4]
    standard["hs6"] = standard.hs_code.astype(str).str[:6]
    rows = []
    for level, column in (("HS4", "hs4"), ("HS6", "hs6"), ("HS10", "hs_code")):
        subset = standard[standard[column].isin(wanted[level])]
        if subset.empty:
            continue
        grouped = subset.groupby(["date", column], as_index=False)["export_value_usd"].sum()
        rows.extend((f"{level}-{item}", str(date), code, int(value)) for date, item, value in grouped.itertuples(index=False, name=None))
    return rows


def merge_periods(snapshot: dict, client: KcsClient, target: str) -> dict:
    """Fetch each period completely, validate, then return an unpublished copy."""
    if next_month(snapshot["asOf"]) != target:
        raise RuntimeError("대시보드 기준월과 갱신 대상월이 연속되지 않습니다.")
    periods = _periods(target)
    expected_months = {f"{year}-{month:02d}" for start, end in periods for year in range(int(start[:4]), int(end[:4]) + 1) for month in range(1, 13) if start <= f"{year}-{month:02d}" <= end}
    summary_parts, item_parts, country_parts = [], [], []
    for start, end in periods:
        summary_parts.append(_api_frame(client, "trade-summary", start, end))
        item_parts.append(_api_frame(client, "item-trade", start, end))
        country_parts.append(_api_frame(client, "country-trade", start, end))
    summary = pd.concat(summary_parts, ignore_index=True)
    items = pd.concat(item_parts, ignore_index=True)
    countries = pd.concat(country_parts, ignore_index=True)
    if set(summary.date.astype(str)) != expected_months or target not in set(items.date.astype(str)) or target not in set(countries.date.astype(str)):
        raise RuntimeError("네 API의 확정월 데이터가 아직 모두 반영되지 않았습니다. 다음 예약 실행에서 재시도합니다.")
    if summary.duplicated("date").any():
        raise RuntimeError("총괄 API에 중복 월이 있습니다.")
    if not {"country_code", "country_name", "export_value_usd"} <= set(countries):
        raise RuntimeError("국가 API 응답에 국가 코드·이름이 없습니다.")
    if not {"hs_code", "export_value_usd"} <= set(items):
        raise RuntimeError("품목 API 응답에 HS코드·수출액이 없습니다.")
    standard_items = items[items.hs_code.astype(str).str.fullmatch(HS10_RE.pattern)]
    standard_countries = countries[countries.country_code.astype(str).str.fullmatch(r"[A-Z]{2}")]
    for date in sorted(expected_months):
        national = _sum_row(summary, "총괄", date)[1]
        item_total = int(standard_items.loc[standard_items.date.astype(str).eq(date), "export_value_usd"].sum())
        country_total = int(standard_countries.loc[standard_countries.date.astype(str).eq(date), "export_value_usd"].sum())
        if national <= 0 or not 0.98 <= item_total / national <= 1.02:
            raise RuntimeError(f"{date}: 품목 API 합계가 전국 수출액과 크게 다릅니다. 응답 누락 여부를 확인하세요.")
        if not 0.98 <= country_total / national <= 1.02:
            raise RuntimeError(f"{date}: 국가 API 합계가 전국 수출액과 크게 다릅니다. 응답 누락 여부를 확인하세요.")
    for code, name in countries[["country_code", "country_name"]].dropna().itertuples(index=False, name=None):
        if re.fullmatch(r"[A-Z]{2}", str(code)) and str(name).strip():
            snapshot["countryNames"][str(code)] = str(name)
    wanted = {level: {key.split("-", 1)[1] for key in snapshot["products"] if key.startswith(level + "-")} for level in ("HS4", "HS6", "HS10")}
    country_codes = sorted({code for code in snapshot["countryNames"] if re.fullmatch(r"[A-Z]{2}", code)})
    country_rows: list[tuple[str, str, str, int]] = []
    for index, code in enumerate(country_codes, 1):
        for start, end in periods:
            frame = _api_frame(client, "item-country-trade", start, end, cntyCd=code)
            country_rows.extend(_country_item_rows(frame, code, wanted))
        if index % 25 == 0 or index == len(country_codes):
            print(f"품목×국가 조회 {index}/{len(country_codes)}개 국가 완료", flush=True)

    industry, subindustry, products = _item_aggregates(items, snapshot)
    snapshot["industryByHS6"] = industry["map"]
    snapshot["itemIndustry"] = item_industries(snapshot["catalog"], industry["map"])
    summary_raw = {row[0]: [row[0], row[1], row[5]] for row in snapshot["summary"] if row[0] not in expected_months}
    for date in sorted(expected_months):
        summary_raw[date] = _sum_row(summary, "총괄", date)
    summary_raw = dict(sorted(summary_raw.items()))
    result_summary = []
    for date, export, imports in summary_raw.values():
        prior = summary_raw.get(previous_month(date, 1))
        year = summary_raw.get(previous_month(date, 12))
        days = calendar.monthrange(int(date[:4]), int(date[5:]))[1]
        result_summary.append([date, export, growth(export, year[1]) if year else None,
                               growth(export, prior[1]) if prior else None,
                               export / days, imports, export - imports])
    snapshot["summary"] = result_summary
    snapshot["hsIndustries"] = sorted([row for row in snapshot["hsIndustries"] if row[0] not in expected_months] + industry["rows"])
    snapshot["subindustries"] = sorted([row for row in snapshot["subindustries"] if row[0] not in expected_months] + subindustry["rows"])
    raw_products = {}
    for key, rows in snapshot["products"].items():
        old = [row[:4] for row in rows if row[0] not in expected_months]
        raw_products[key] = sorted(old + products.get(key, []))
    snapshot["products"], snapshot["productQuarterly"] = enrich_products(raw_products)
    replacement_country: dict[str, list[list]] = defaultdict(list)
    unique_country_rows = set()
    for key, date, code, value in country_rows:
        pair = (key, date, code)
        if pair in unique_country_rows:
            raise RuntimeError(f"품목×국가 API에 중복이 있습니다: {key}/{date}/{code}")
        unique_country_rows.add(pair)
        replacement_country[key].append([date, code, value])
    country_start = f"{int(target[:4]) - 3}-01"
    for key, rows in snapshot["countryItems"].items():
        snapshot["countryItems"][key] = sorted(
            [row for row in rows if row[0] >= country_start and row[0] not in expected_months]
            + replacement_country[key]
        )
    # A partial country endpoint response can otherwise pass the upper-bound
    # validator. Latest-month country totals must match each traded product.
    for key, rows in snapshot["products"].items():
        current = next((row[1] for row in rows if row[0] == target), 0)
        if current <= 0:
            continue
        country_total = sum(row[2] for row in snapshot["countryItems"][key] if row[0] == target)
        if abs(country_total - current) > max(1, current * 0.001):
            raise RuntimeError(f"{key} {target}: 국가별 합계가 품목 수출액과 다릅니다. 웹 파일은 유지됩니다.")
    snapshot["asOf"] = target
    snapshot["industryCommonAsOf"] = min(target, snapshot["industryAsOf"])
    snapshot["status"] = "final"
    snapshot["updatedAt"] = datetime.now(timezone.utc).isoformat()
    snapshot["snapshotBuiltAt"] = snapshot["updatedAt"]
    return snapshot


def _publish(snapshot: dict, snapshot_path: Path, catalog: list[dict], rules: dict) -> None:
    validate_snapshot(snapshot, source_catalog=catalog, source_industry_rules=rules)
    temporary = snapshot_path.with_name(f".{snapshot_path.name}.tmp")
    try:
        temporary.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temporary.replace(snapshot_path)
    finally:
        temporary.unlink(missing_ok=True)


def run(snapshot_path: Path = SNAPSHOT, *, client: KcsClient | None = None, session: requests.Session | None = None) -> str:
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    catalog = load_catalog(CATALOG)
    rules = load_rules(RULES_PATH)
    metadata_changed = _metadata_sync(snapshot, catalog, rules)
    validate_snapshot(snapshot, source_catalog=catalog, source_industry_rules=rules)
    target = next_month(snapshot["asOf"])
    today = datetime.now(timezone.utc).date()
    if target >= f"{today.year:04d}-{today.month:02d}":
        if metadata_changed:
            snapshot["snapshotBuiltAt"] = datetime.now(timezone.utc).isoformat()
            _publish(snapshot, snapshot_path, catalog, rules)
            return "표시 매핑만 반영했습니다. 새 확정월은 아직 종료되지 않았습니다."
        return f"대기: {target} 월이 아직 끝나지 않았습니다."
    release = final_release_url(target, session=session)
    if release is None:
        if metadata_changed:
            snapshot["snapshotBuiltAt"] = datetime.now(timezone.utc).isoformat()
            _publish(snapshot, snapshot_path, catalog, rules)
            return f"표시 매핑만 반영했습니다. 관세청 {target} 확정치 발표 전입니다."
        return f"대기: 관세청 {target} 월 확정치 발표 전입니다."
    print(f"관세청 {target} 확정치 발표 확인: {release}", flush=True)
    api = client or KcsClient(load_api_key(), timeout=180)
    updated = merge_periods(snapshot, api, target)
    updated["sourceReleaseUrl"] = release
    _publish(updated, snapshot_path, catalog, rules)
    return f"완료: {target} 확정치 웹 데이터 갱신·검증"


def main() -> None:
    parser = argparse.ArgumentParser(description="관세청 확정치 발표 확인 후 GitHub 웹 데이터 갱신")
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    print(run(args.snapshot))


if __name__ == "__main__":
    main()
