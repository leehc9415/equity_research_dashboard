"""KRX 정규장 교정 미리보기 (깃허브 Actions 에서 실행, 결과는 아티팩트로만 남김 — 커밋·배포·상태 저장 안 함).

  1) 2026-09-14 ~ 오늘을 KRX 정규장 시세로 덮어쓰기
  2) 스탁이지 현재가(정규장 종가)와 비교, 기준가 연속성 확인
  3) 지표·대시보드 데이터 재생성 → out/ , DB 스냅샷 → data/market-state-preview.zip
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud_update import rebuild, publish_export  # noqa: E402
from cloud_state import pack  # noqa: E402
from src.prices import krx_official  # noqa: E402
from src.prices.db import connect, DB_PATH  # noqa: E402

# 스탁이지 스크리너 화면의 현재가(장 마감 기준) — 교차검증으로 모은 값
SE_CLOSE = [["2026-11-05", "000810", 652000], ["2026-11-05", "003490", 30350], ["2026-11-05", "004080", 14540], ["2026-11-05", "012030", 2245], ["2026-11-05", "017890", 16010], ["2026-11-05", "021240", 109800], ["2026-11-05", "028050", 64300], ["2026-11-05", "033780", 194200], ["2026-11-05", "036800", 7860], ["2026-11-05", "044450", 11640], ["2026-11-05", "051160", 13530], ["2026-11-05", "055490", 20000], ["2026-11-05", "071200", 11490], ["2026-11-05", "079940", 49400], ["2026-11-05", "089860", 52900], ["2026-11-05", "091580", 26000], ["2026-11-05", "092730", 24300], ["2026-11-05", "093320", 175300], ["2026-11-05", "100120", 29200], ["2026-11-05", "130580", 6890], ["2026-11-05", "138040", 135000], ["2026-11-05", "282330", 150200], ["2026-11-05", "453340", 20150], ["2026-11-06", "004080", 14550], ["2026-11-06", "017890", 15500], ["2026-11-06", "021240", 107600], ["2026-11-06", "028050", 60500], ["2026-11-06", "033780", 189900], ["2026-11-06", "050120", 3765], ["2026-11-06", "051160", 13460], ["2026-11-06", "055490", 18750], ["2026-11-06", "071200", 11260], ["2026-11-06", "079940", 49400], ["2026-11-06", "089860", 58400], ["2026-11-06", "092730", 23500], ["2026-11-06", "093320", 175000], ["2026-11-06", "121440", 8840], ["2026-11-06", "130580", 6760], ["2026-11-06", "138040", 132200], ["2026-11-06", "282330", 143400], ["2026-11-06", "453340", 20250], ["2026-11-09", "004080", 14570], ["2026-11-09", "008370", 5490], ["2026-11-09", "012030", 2155], ["2026-11-09", "017890", 15440], ["2026-11-09", "028050", 60600], ["2026-11-09", "033780", 182400], ["2026-11-09", "051160", 13800], ["2026-11-09", "077500", 10270], ["2026-11-09", "079940", 49350], ["2026-11-09", "089860", 58400], ["2026-11-09", "092730", 23350], ["2026-11-09", "100120", 27850], ["2026-11-09", "121440", 8810], ["2026-11-09", "130580", 6890], ["2026-11-09", "138040", 128500], ["2026-11-09", "282330", 139200], ["2026-11-09", "453340", 20100]]


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
    con = connect(DB_PATH)
    con.execute("SET threads=2; SET memory_limit='4GB'; SET TimeZone='Asia/Seoul'")
    codes = con.execute("""SELECT DISTINCT symbol_id FROM prices_daily WHERE date >= '2026-08-01'
        AND source IN ('marcap', 'naver', 'naver_fix')""").df()["symbol_id"].tolist()
    before = con.execute("SELECT symbol_id, date, close FROM prices_daily WHERE date >= '2026-09-14' AND length(symbol_id)=6").df()
    new = krx_official.overlay(con, sorted(codes), "2026-09-14", pd.Timestamp.now(tz="Asia/Seoul").tz_localize(None).normalize())
    if new.empty:
        raise SystemExit("KRX 에서 받은 데이터가 없음 (로그인/접속 확인)")

    print("\n[1] 스탁이지 현재가 vs 교정 후 종가")
    ok = bad = miss = 0
    for d, c, px in SE_CLOSE:
        r = con.execute("SELECT close, source FROM prices_daily WHERE symbol_id=? AND date=?", [c, d]).fetchone()
        if r is None:
            miss += 1
        elif abs(r[0] - px) < 0.5:
            ok += 1
        else:
            bad += 1
            print(f"  불일치 {d} {c}: 스탁이지 {px:,} / 교정 {r[0]:,.0f} ({r[1]})")
    print(f"  일치 {ok} / 불일치 {bad} / 없음 {miss}")

    print("\n[2] 기준가 = 전일 종가 인지 (날짜별 불일치 종목 수, 권리 변동 제외하면 0~몇 개여야 함)")
    print(con.execute("""WITH x AS (SELECT symbol_id, date, source, ref_price,
            lag(close) OVER (PARTITION BY symbol_id ORDER BY date) pc FROM prices_daily WHERE length(symbol_id)=6)
        SELECT date, count(*) FILTER (WHERE abs(ref_price/pc-1) > 1e-9) AS mismatch, count(*) n
        FROM x WHERE date >= '2026-09-14' AND pc > 0 AND source='krx' GROUP BY 1 ORDER BY 1""").df().to_string())

    print("\n[3] 교정 전후 종가 차이 (날짜별)")
    after = con.execute("SELECT symbol_id, date, close FROM prices_daily WHERE date >= '2026-09-14' AND length(symbol_id)=6").df()
    m = before.merge(after, on=["symbol_id", "date"], suffixes=("_old", "_new"))
    m["d"] = (m["close_new"] / m["close_old"] - 1).abs()
    print(m.groupby("date").agg(n=("d", "size"), same=("d", lambda s: round((s < 1e-9).mean(), 2)),
                                over1pct=("d", lambda s: int((s > 0.01).sum())), max_pct=("d", lambda s: round(s.max() * 100, 1))).to_string())

    rebuild(con)
    out.mkdir(parents=True, exist_ok=True)
    publish_export(con, out, kr=True)
    con.execute("CHECKPOINT")
    con.close()
    pack(DB_PATH, DB_PATH.parent / "market-state-preview.zip")
    print("미리보기 완료:", out)


if __name__ == "__main__":
    main()
