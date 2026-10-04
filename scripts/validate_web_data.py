"""Validate the published dashboard bundle and its editable source mapping."""

from __future__ import annotations

import argparse
from pathlib import Path

from catalog_mapping import CATALOG, load
from industry_rules import RULES_PATH, load_rules
from web_snapshot import validate_file


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT = ROOT / "dashboard" / "data" / "dashboard-data.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate dashboard web data and mapping consistency")
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--industry-rules", type=Path, default=RULES_PATH)
    args = parser.parse_args()
    report = validate_file(args.snapshot, source_catalog=load(args.catalog), source_industry_rules=load_rules(args.industry_rules))
    print(f"웹 데이터 검증 완료: 형식 v{report['schemaVersion']}, 기준월 {report['asOf']}, 활성 품목 {report['activeItems']}개, 국가별 행 {report['countryRows']:,}개")


if __name__ == "__main__":
    main()
