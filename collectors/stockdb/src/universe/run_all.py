"""유니버스 구축 전체를 한 번에 실행한다.

    python -m src.universe.run_all

  1) 설정 검증
  2) 네이버 업종 구성종목 수집      → data/universe/naver_upjong_members.csv
  3) KRX 상장종목 스냅샷 + 섹터 맵  → data/universe/kr_sector_map.csv, kr_sector_report.md
  4) DuckDB 적재                    → data/market.duckdb

이미 수집한 네이버 CSV를 재사용하려면 --skip-naver.
"""
from __future__ import annotations

import argparse

import pandas as pd

from . import build_kr_sector_map as bsm
from .config import load_universe
from .kr_listing import load_listing
from .load_universe import DB_PATH, load
from .naver_upjong import OUT_CSV as MEMBERS_CSV, collect


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-naver", action="store_true", help="기존 네이버 CSV 재사용")
    ap.add_argument("--sleep", type=float, default=0.8)
    a = ap.parse_args()

    print("① 설정 검증")
    uni = load_universe()
    if uni.errors:
        raise SystemExit("설정 오류:\n  " + "\n  ".join(uni.errors))

    if a.skip_naver and MEMBERS_CSV.exists():
        print(f"② 네이버 업종: 기존 파일 사용 ({MEMBERS_CSV})")
        members = pd.read_csv(MEMBERS_CSV, dtype={"code": str})
    else:
        print("② 네이버 업종 수집")
        members = collect(sleep=a.sleep)
        MEMBERS_CSV.parent.mkdir(parents=True, exist_ok=True)
        members.to_csv(MEMBERS_CSV, index=False, encoding="utf-8-sig")

    print("③ 상장종목 스냅샷 + 섹터 맵")
    listing = load_listing()
    df, report = bsm.build(uni, members, listing)
    df.to_csv(bsm.OUT_MAP, index=False, encoding="utf-8-sig")
    bsm.OUT_REPORT.write_text(report, encoding="utf-8")

    print("④ DuckDB 적재")
    res = load(uni, DB_PATH, bsm.OUT_MAP)
    for gid, n, inf in res["groups"]:
        print(f"   {gid:<14} {n:>3}개" + (f" (추정 {inf}개)" if inf else ""))
    print(f"   한국 섹터 편입 종목 {res['kr_stocks']}개\n")
    print(report)
    print(f"리포트 파일: {bsm.OUT_REPORT}")


if __name__ == "__main__":
    main()
