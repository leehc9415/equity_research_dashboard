"""Validated official MOTIR 20-industry monthly series for the web dashboard."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "data" / "reference" / "motir20"
LATEST_PATH = SOURCE_DIR / "motir20_monthly_latest.json"
HISTORICAL_PATH = SOURCE_DIR / "hs10_static_monthly_202201_202504.json"
SNAPSHOTS_PATH = SOURCE_DIR / "motir20_monthly_snapshots.json"
VALIDATION_PATH = SOURCE_DIR / "motir20_backfill_validation.json"

CLASSIFICATION = "MOTIR_20_MAIN_EXPORTS_2026"
INDUSTRY_ORDER = [
    "반도체", "자동차", "자동차부품", "선박", "석유제품", "석유화학",
    "철강", "일반기계", "디스플레이", "무선통신기기", "컴퓨터", "전기기기",
    "이차전지", "바이오헬스", "화장품", "농수산식품", "가전", "섬유",
    "비철금속", "생활용품",
]

# Official MTI headlines are never reconciled to these broad HS groups.  The
# bridge only selects the already-existing HS reference rows shown below the
# official chart.
HS_REFERENCE_GROUPS = {
    "반도체": ["반도체·IT"],
    "자동차": ["자동차"],
    "자동차부품": ["자동차"],
    "선박": ["조선"],
    "석유제품": ["석유·화학"],
    "석유화학": ["석유·화학"],
    "철강": ["철강·금속"],
    "일반기계": ["기계·전기"],
    "디스플레이": ["반도체·IT"],
    "무선통신기기": ["반도체·IT"],
    "컴퓨터": ["반도체·IT"],
    "전기기기": ["기계·전기"],
    "이차전지": ["배터리"],
    "바이오헬스": ["바이오·정밀"],
    "화장품": ["소비재·기타"],
    "농수산식품": ["식품·농수산"],
    "가전": ["소비재·기타"],
    "섬유": ["섬유·의류"],
    "비철금속": ["철강·금속"],
    "생활용품": ["소비재·기타"],
}

CATALOG_GROUPS = {
    "반도체": ["반도체"],
    "자동차": ["자동차"],
    "자동차부품": ["자동차"],
    "선박": ["조선"],
    "석유제품": ["정유화학"],
    "석유화학": ["정유화학"],
    "철강": ["철강"],
    "일반기계": ["산업재", "반도체 장비"],
    "디스플레이": ["전기전자"],
    "무선통신기기": ["통신장비"],
    "컴퓨터": ["반도체", "전기전자"],
    "전기기기": ["전력", "전기전자"],
    "이차전지": ["2차전지"],
    "바이오헬스": ["바이오", "의료기기"],
    "화장품": ["화장품"],
    "농수산식품": ["음식료"],
    "가전": ["전기전자"],
    "섬유": [],
    "비철금속": ["철강"],
    "생활용품": [],
}


def _next_month(month: str) -> str:
    year, number = map(int, month.split("-"))
    index = year * 12 + number
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def load_official_series(
    latest_path: Path = LATEST_PATH,
    validation_path: Path = VALIDATION_PATH,
    historical_path: Path = HISTORICAL_PATH,
) -> dict:
    rows = json.loads(latest_path.read_text(encoding="utf-8"))
    report = json.loads(validation_path.read_text(encoding="utf-8"))
    if report.get("status") != "pass":
        raise ValueError("MTI 20개 산업 원본 검증 상태가 pass가 아닙니다.")
    if not isinstance(rows, list) or len(rows) != 340:
        raise ValueError("MTI 20개 산업 latest 원본은 340행이어야 합니다.")

    seen: set[tuple[str, str]] = set()
    months: set[str] = set()
    names: set[str] = set()
    by_industry: dict[str, list[dict]] = {name: [] for name in INDUSTRY_ORDER}
    for index, row in enumerate(rows):
        month, name = row.get("month"), row.get("item")
        if not isinstance(month, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise ValueError(f"MTI 원본 {index}: 월 형식 오류")
        if name not in INDUSTRY_ORDER or (month, name) in seen:
            raise ValueError(f"MTI 원본 {index}: 산업명 또는 중복 오류 ({month}/{name})")
        if row.get("classification") != CLASSIFICATION:
            raise ValueError(f"MTI 원본 {index}: 분류 버전 오류")
        for key in ("export_musd", "yoy_pct_source"):
            value = row.get(key)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"MTI 원본 {index}: {key} 숫자 오류")
        seen.add((month, name))
        months.add(month)
        names.add(name)
        by_industry[name].append(row)

    ordered_months = sorted(months)
    if ordered_months[0] != "2025-05" or ordered_months[-1] != "2026-09" or len(ordered_months) != 17:
        raise ValueError("MTI 원본 범위는 2025-05~2026-09의 17개월이어야 합니다.")
    if any(_next_month(left) != right for left, right in zip(ordered_months, ordered_months[1:])):
        raise ValueError("MTI 원본 월이 연속되지 않습니다.")
    if names != set(INDUSTRY_ORDER) or any(len(values) != 17 for values in by_industry.values()):
        raise ValueError("MTI 원본은 매월 20개 산업을 모두 포함해야 합니다.")

    historical = json.loads(historical_path.read_text(encoding="utf-8"))
    if not isinstance(historical, list) or len(historical) != 800:
        raise ValueError("HS10 고정 매핑 과거자료는 800행이어야 합니다.")
    historical_months = sorted({row.get("month") for row in historical})
    if historical_months[0] != "2022-01" or historical_months[-1] != "2025-04" or len(historical_months) != 40:
        raise ValueError("HS10 고정 매핑 과거자료 범위는 2022-01~2025-04이어야 합니다.")
    if any(_next_month(left) != right for left, right in zip(historical_months, historical_months[1:])):
        raise ValueError("HS10 고정 매핑 과거자료 월이 연속되지 않습니다.")
    historical_by_industry: dict[str, list[dict]] = {name: [] for name in INDUSTRY_ORDER}
    historical_keys = set()
    for index, row in enumerate(historical):
        month, name = row.get("month"), row.get("item")
        value = row.get("export_usd")
        if month not in historical_months or name not in INDUSTRY_ORDER or (month, name) in historical_keys:
            raise ValueError(f"HS10 과거자료 {index}: 월·산업 또는 중복 오류")
        if type(value) is not int or value < 0 or row.get("classification") != CLASSIFICATION:
            raise ValueError(f"HS10 과거자료 {index}: 금액·분류 오류")
        if row.get("source_type") != "calculated_static_2026_hs10":
            raise ValueError(f"HS10 과거자료 {index}: 산출 방식 오류")
        historical_keys.add((month, name))
        historical_by_industry[name].append(row)
    if any(len(values) != 40 for values in historical_by_industry.values()):
        raise ValueError("HS10 과거자료는 매월 20개 산업을 모두 포함해야 합니다.")

    output = []
    for name in INDUSTRY_ORDER:
        previous = None
        for row in sorted(historical_by_industry[name], key=lambda value: value["month"]):
            export_usd = row["export_usd"]
            source_mom = row.get("mom_pct")
            expected_mom = export_usd / previous - 1 if previous else None
            mom = source_mom / 100 if source_mom is not None else None
            if mom is None and expected_mom is not None or mom is not None and expected_mom is None:
                raise ValueError(f"HS10 과거자료 MoM 누락 오류: {row['month']}/{name}")
            if mom is not None and not math.isclose(mom, expected_mom, abs_tol=1e-10):
                raise ValueError(f"HS10 과거자료 MoM 검산 오류: {row['month']}/{name}")
            source_yoy = row.get("yoy_pct_calculated")
            yoy = source_yoy / 100 if source_yoy is not None else None
            output.append([row["month"], name, export_usd, yoy, mom])
            previous = export_usd
        for row in sorted(by_industry[name], key=lambda value: value["month"]):
            export_usd = int(round(row["export_musd"] * 1_000_000))
            source_mom = row.get("mom_pct")
            expected_mom = export_usd / previous - 1 if previous else None
            if source_mom is None:
                mom = expected_mom
            else:
                mom = source_mom / 100
                if expected_mom is not None and not math.isclose(mom, expected_mom, abs_tol=0.0002):
                    raise ValueError(f"MTI 원본 MoM 검산 오류: {row['month']}/{name}")
            output.append([row["month"], name, export_usd, row["yoy_pct_source"] / 100, mom])
            previous = export_usd

    return {
        "names": INDUSTRY_ORDER,
        "rows": sorted(output),
        "coverageStart": historical_months[0],
        "coverageEnd": ordered_months[-1],
        "rowCount": len(output),
        "classification": CLASSIFICATION,
        "method": "static-2026-hs10-backfill-plus-official-motir-20-latest-release",
        "precedenceRule": report.get("precedence_rule"),
        "hsReferenceGroups": HS_REFERENCE_GROUPS,
        "catalogGroups": CATALOG_GROUPS,
        "quality": {
            "sourceBoundary": {
                "historicalFrom": historical_months[0],
                "historicalTo": historical_months[-1],
                "historicalMethod": "2026 HS10 mapping applied unchanged to historical Customs exports",
                "officialFrom": ordered_months[0],
                "warning": "Historical HS10 code changes are not adjusted; historical industry values can be understated.",
            },
            "reconciliation": [{
                "month": "2026-08",
                "industry": "석유화학",
                "officialUsd": 3_877_795_901,
                "hsMappedUsd": 3_852_654_301,
                "differenceUsd": 25_141_600,
                "differenceFraction": 25_141_600 / 3_877_795_901,
                "status": "reconciliation_required",
            }],
        },
    }
