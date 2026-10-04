"""Command line interface for small sample collections."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from .api_catalog import ENDPOINTS
from .backfill import backfill_endpoint, backfill_item_country
from .client import ConfigurationError, KcsClient, collect_endpoint, load_api_key
from .dashboard_timeseries import build_dashboard_timeseries
from .hs6 import build_hs6_dataset
from .monthly_update import update_all_export_timeseries
from .parser import ApiResponseError, parse_file, profile_frame
from .timeseries import build_item_timeseries, fetch_and_update_item_timeseries


def _month(value: str) -> str:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        raise argparse.ArgumentTypeError("YYYY-MM 형식이어야 합니다.")
    return value


def _hs_code(value: str) -> str:
    if not re.fullmatch(r"\d{2}|\d{4}|\d{6}|\d{10}", value):
        raise argparse.ArgumentTypeError("HS 코드는 2, 4, 6, 10자리 숫자여야 합니다.")
    return value


def _country_code(value: str) -> str:
    value = value.upper()
    if not re.fullmatch(r"[A-Z]{2}", value):
        raise argparse.ArgumentTypeError("국가 코드는 영문 2자리여야 합니다.")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="관세청 월간 수출입 API 소량 수집기")
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="API 하나 수집")
    collect.add_argument("endpoint", choices=ENDPOINTS)
    _add_collection_args(collect)

    collect_all = sub.add_parser("collect-all", help="4개 API 모두 수집")
    _add_collection_args(collect_all)

    backfill = sub.add_parser("backfill", help="연도별 분할로 전체 데이터 백필")
    backfill.add_argument(
        "--endpoint",
        action="append",
        choices=("trade-summary", "item-trade", "country-trade"),
        default=[],
        help="생략하면 3개 전체조회 가능 API를 모두 수집합니다.",
    )
    backfill.add_argument("--start-month", required=True, type=_month)
    backfill.add_argument("--end-month", required=True, type=_month)
    backfill.add_argument("--output-root", type=Path, default=Path("data"))
    backfill.add_argument("--timeout", type=float, default=180.0)
    backfill.add_argument("--force", action="store_true")

    item_country = sub.add_parser(
        "backfill-item-country", help="국가 목록을 이용해 품목×국가 전체 백필"
    )
    item_country.add_argument("--start-month", required=True, type=_month)
    item_country.add_argument("--end-month", required=True, type=_month)
    item_country.add_argument("--output-root", type=Path, default=Path("data"))
    item_country.add_argument("--timeout", type=float, default=180.0)
    item_country.add_argument("--force", action="store_true")
    item_country.add_argument("--min-free-gb", type=float, default=5.0)
    item_country.add_argument("--country-code", action="append", type=_country_code, default=[])

    inspect = sub.add_parser("inspect", help="저장된 raw XML 구조 분석")
    inspect.add_argument("xml", type=Path)
    inspect.add_argument("--endpoint", choices=ENDPOINTS, default="item-country-trade")

    build_hs6 = sub.add_parser("build-hs6", help="전체 품목 백필에서 월별 HS6 데이터 생성")
    build_hs6.add_argument("--data-root", type=Path, default=Path("data"))

    build_timeseries = sub.add_parser(
        "build-item-timeseries", help="품목별 월간·분기 대시보드 시계열 생성"
    )
    build_timeseries.add_argument("--data-root", type=Path, default=Path("data"))

    update_timeseries = sub.add_parser(
        "update-item-timeseries",
        help="원본을 추가 저장하지 않고 품목 시계열을 API 응답으로 직접 갱신",
    )
    update_timeseries.add_argument("--start-month", required=True, type=_month)
    update_timeseries.add_argument("--end-month", required=True, type=_month)
    update_timeseries.add_argument("--data-root", type=Path, default=Path("data"))
    update_timeseries.add_argument("--timeout", type=float, default=180.0)

    update_all = sub.add_parser(
        "update-export-timeseries",
        help="4개 수출입 API를 조회해 모든 대시보드 시계열을 한 번에 갱신",
    )
    update_all.add_argument("--start-month", required=True, type=_month)
    update_all.add_argument("--end-month", required=True, type=_month)
    update_all.add_argument("--data-root", type=Path, default=Path("data"))
    update_all.add_argument("--timeout", type=float, default=180.0)

    remaining_timeseries = sub.add_parser(
        "build-dashboard-timeseries",
        help="수출입총괄·국가·품목×국가 대시보드 시계열 생성",
    )
    remaining_timeseries.add_argument("--data-root", type=Path, default=Path("data"))
    return parser


def _add_collection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--start-month", required=True, type=_month)
    parser.add_argument("--end-month", required=True, type=_month)
    parser.add_argument("--hs-code", action="append", type=_hs_code, default=[])
    parser.add_argument("--country-code", action="append", type=_country_code, default=[])
    parser.add_argument("--output-root", type=Path, default=Path("data"))
    parser.add_argument("--timeout", type=float, default=30.0)


def _validate_period(start: str, end: str) -> None:
    start_dt = datetime.strptime(start, "%Y-%m")
    end_dt = datetime.strptime(end, "%Y-%m")
    months = (end_dt.year - start_dt.year) * 12 + end_dt.month - start_dt.month
    if months < 0:
        raise ConfigurationError("종료월은 시작월보다 빠를 수 없습니다.")
    if months > 11:
        raise ConfigurationError("공식 명세에 따라 한 요청의 조회기간은 1년 이내여야 합니다.")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    try:
        if args.command == "inspect":
            parsed = parse_file(args.xml, ENDPOINTS[args.endpoint])
            print(json.dumps(profile_frame(parsed.frame), ensure_ascii=False, indent=2))
            return 0
        if args.command == "build-hs6":
            print(json.dumps(build_hs6_dataset(args.data_root), ensure_ascii=False, indent=2))
            return 0
        if args.command == "build-item-timeseries":
            print(json.dumps(build_item_timeseries(args.data_root), ensure_ascii=False, indent=2))
            return 0
        if args.command == "build-dashboard-timeseries":
            result = build_dashboard_timeseries(
                args.data_root,
                progress=lambda payload: print(
                    json.dumps(payload, ensure_ascii=False), flush=True
                ),
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        if args.command not in ("backfill", "backfill-item-country"):
            _validate_period(args.start_month, args.end_month)
        client = KcsClient(load_api_key(), timeout=args.timeout)
        if args.command == "update-export-timeseries":
            result = update_all_export_timeseries(
                client,
                start_month=args.start_month,
                end_month=args.end_month,
                data_root=args.data_root,
                progress=lambda payload: print(
                    json.dumps(payload, ensure_ascii=False), flush=True
                ),
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "update-item-timeseries":
            print(
                json.dumps(
                    fetch_and_update_item_timeseries(
                        client,
                        start_month=args.start_month,
                        end_month=args.end_month,
                        data_root=args.data_root,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command == "backfill":
            slugs = args.endpoint or ["trade-summary", "item-trade", "country-trade"]
            for slug in slugs:
                parts = backfill_endpoint(
                    client,
                    slug,
                    start_month=args.start_month,
                    end_month=args.end_month,
                    output_root=args.output_root,
                    force=args.force,
                )
                for part in parts:
                    print(json.dumps(part.__dict__, ensure_ascii=False))
            return 0
        if args.command == "backfill-item-country":
            country_codes = args.country_code or _load_country_codes(args.output_root)
            completed = 0
            rows = 0
            for part in backfill_item_country(
                client,
                start_month=args.start_month,
                end_month=args.end_month,
                country_codes=country_codes,
                output_root=args.output_root,
                force=args.force,
                min_free_gb=args.min_free_gb,
            ):
                completed += 1
                if part.rows > 0:
                    rows += part.rows
                if completed % 25 == 0:
                    print(json.dumps({"completed_parts": completed, "new_rows": rows}, ensure_ascii=False), flush=True)
            print(json.dumps({"completed_parts": completed, "new_rows": rows, "done": True}, ensure_ascii=False))
            return 0
        slugs = list(ENDPOINTS) if args.command == "collect-all" else [args.endpoint]
        for slug in slugs:
            result = collect_endpoint(
                client,
                ENDPOINTS[slug],
                start_month=args.start_month,
                end_month=args.end_month,
                hs_codes=args.hs_code,
                country_codes=args.country_code,
                output_root=args.output_root,
            )
            print(
                json.dumps(
                    {
                        "endpoint": result.endpoint,
                        "rows": result.rows,
                        "columns": result.columns,
                        "raw_files": len(result.raw_paths),
                        "csv": str(result.csv_path),
                    },
                    ensure_ascii=False,
                )
            )
        return 0
    except (ConfigurationError, ApiResponseError, RuntimeError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2


def _load_country_codes(output_root: Path) -> list[str]:
    import pandas as pd

    paths = sorted((output_root / "local" / "backfill" / "country-trade").glob("year=*/*.parquet"))
    if not paths:
        raise ConfigurationError("국가 전체 백필이 없어 국가 코드 목록을 만들 수 없습니다.")
    codes: set[str] = set()
    for path in paths:
        frame = pd.read_parquet(path, columns=["country_code"])
        codes.update(frame["country_code"].dropna().astype(str))
    return sorted(code for code in codes if re.fullmatch(r"[A-Z]{2}", code))


if __name__ == "__main__":
    raise SystemExit(main())
