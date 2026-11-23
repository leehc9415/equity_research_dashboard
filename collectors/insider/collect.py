"""내부자 거래공시 수집기 (1단계).

사용 예
  python collect.py                       # 최근 3일 다시 수집 + feed 갱신
  python collect.py --date 20261106       # 하루
  python collect.py --from 20260901 --to 20261106   # 기간(이미 받은 날은 건너뜀)
  python collect.py --feed-only           # 수집 없이 feed만 다시 만들기

API 키는 환경변수 OPENDART_API_KEY 또는 이 폴더의 .env 파일에서 읽는다.
"""
import argparse
import datetime as dt
import sys
from pathlib import Path

from insider.dart import Dart, LimitExceeded, load_api_key
from insider.feed import build_feed
from insider.pipeline import Store, collect_day

ROOT = Path(__file__).resolve().parent
KST = dt.timezone(dt.timedelta(hours=9))


def daterange(a: dt.date, b: dt.date):
    d = a
    while d <= b:
        yield d
        d += dt.timedelta(days=1)


def main(argv=None):
    ap = argparse.ArgumentParser(description="OpenDART 내부자 거래공시 수집")
    ap.add_argument("--date")
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="end")
    ap.add_argument("--recent", type=int, default=3, help="인자 없이 실행할 때 다시 받을 최근 일수")
    ap.add_argument("--force", action="store_true", help="이미 받은 날도 다시 수집")
    ap.add_argument("--feed-days", type=int, default=120, help="feed에 담을 기간(일)")
    ap.add_argument("--feed-only", action="store_true")
    ap.add_argument("--no-feed", action="store_true")
    ap.add_argument("--price-db", help="종가 DuckDB 경로(기본: ../data/market.duckdb 또는 STOCKDB_DB)")
    ap.add_argument("--no-estimate", action="store_true", help="단가 없는 거래의 종가 추정 안 함")
    ap.add_argument("--workers", type=int, default=4, help="원문 동시 다운로드 수")
    ap.add_argument("--no-raw", action="store_true", help="공시 원문 캐시를 저장하지 않음")
    a = ap.parse_args(argv)

    today = dt.datetime.now(KST).date()
    store = Store(ROOT, keep_raw=not a.no_raw)

    if not a.feed_only:
        dart = Dart(load_api_key(ROOT))
        if a.date:
            dates, force = [dt.datetime.strptime(a.date, "%Y%m%d").date()], True
        elif a.start:
            s = dt.datetime.strptime(a.start, "%Y%m%d").date()
            e = dt.datetime.strptime(a.end, "%Y%m%d").date() if a.end else today
            dates, force = list(daterange(s, e)), a.force
        else:
            dates, force = [today - dt.timedelta(days=i) for i in range(a.recent - 1, -1, -1)], True
        for d in dates:
            ds = f"{d:%Y%m%d}"
            if not force and store.day_path(ds).exists():
                continue
            try:
                collect_day(dart, store, ds, workers=a.workers)
            except LimitExceeded:
                print("OpenDART 일일 호출 한도에 걸렸습니다. 여기까지 저장하고 멈춥니다. "
                      "다음 실행 때 이어서 받습니다.", file=sys.stderr)
                break
        print(f"API 호출 {dart.calls}회")

    if not a.no_feed:
        from insider.prices import default_db_path
        price_db = None if a.no_estimate else (Path(a.price_db) if a.price_db else default_db_path(ROOT))
        build_feed(ROOT, days=a.feed_days, end=today, price_db=price_db)


if __name__ == "__main__":
    main()
