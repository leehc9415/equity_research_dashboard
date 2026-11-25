"""키움 일봉 파서·수정주가 뷰 테스트 (네트워크 없이 실행).

    python -m pytest -q tests/test_kiwoom_kr.py
"""
import duckdb
import pytest

from src.prices.kiwoom_kr import DDL, parse_rows, upsert

# 183300 원주가 실제 응답(2026-11-15 조회), 2026-08-21 액면분할(1→2.5주 상당) 포함. 최신일이 먼저 온다.
ROWS = [
    {"cur_prc": "25400", "trde_qty": "1000495", "trde_prica": "25512", "dt": "20260824",
     "open_pric": "26200", "high_pric": "26525", "low_pric": "24800", "pred_pre": "-2300"},
    {"cur_prc": "27700", "trde_qty": "1595930", "trde_prica": "43759", "dt": "20260821",
     "open_pric": "26900", "high_pric": "28300", "low_pric": "25800", "pred_pre": "+5900", "upd_rt": "-59.93"},
    {"cur_prc": "54400", "trde_qty": "0", "trde_prica": "0", "dt": "20260820",
     "open_pric": "54400", "high_pric": "54400", "low_pric": "54400", "pred_pre": "0"},
    {"cur_prc": "-54400", "trde_qty": "0", "trde_prica": "0", "dt": "20260819",
     "open_pric": "0", "high_pric": "0", "low_pric": "0", "pred_pre": "0"},
]


def test_parse_rows():
    df = parse_rows(ROWS, "183300", "KRX", "2026-08-20").set_index("date")
    assert len(df) == 3                                   # 08-19 는 start 이전
    r = df.loc[df.index.max()]
    assert (r.close, r.ref_price, r.volume, r.value) == (25400, 27700, 1000495, 25512e6)
    split = df.iloc[1]
    assert split.ref_price == 21800                       # 27700 - 5900


def test_zero_and_sign():
    df = parse_rows(ROWS, "183300", "KRX", "2026-08-01")
    r = df[df["date"].astype(str) == "2026-08-19"].iloc[0]
    assert r.close == 54400 and r.open == 54400           # 부호 제거, 0 → 종가


def test_sync_prices_daily():
    """키움 KRX → prices_daily: 같은 기간 기존 행 교체, 다른 종목 유지, 수정계수·기준가 연쇄, 주식수·거래대금 채움."""
    from src.prices.db import DDL as DB_DDL
    from src.prices.kr import recompute_adj_factor
    from src.prices.kiwoom_kr import sync_prices_daily
    con = duckdb.connect()
    con.execute(DB_DDL)
    con.execute(DDL)
    upsert(con, parse_rows(ROWS, "183300", "KRX", "2026-08-01"))
    upsert(con, parse_rows(ROWS, "183300", "AL", "2026-08-01"))                 # AL 은 옮기지 않음
    con.execute("""INSERT INTO prices_daily (symbol_id, date, close, value, shares, source) VALUES
        ('183300', '2026-08-19', 1, 123456789, 1000, 'marcap'),     -- 키움 값으로 바뀌되 거래대금·주식수 유지
        ('183300', '2026-08-20', 2, NULL, NULL, 'naver_fix'),
        ('999990', '2026-08-20', 5, NULL, NULL, 'marcap')""")          # 키움에 없는 종목 → 그대로
    con.execute("INSERT INTO kiwoom_listing VALUES ('2026-08-24', '183300', 'x', '코스닥', 2500, FALSE)")
    assert sync_prices_daily(con) == 4
    p = con.execute("""SELECT strftime(date, '%m%d'), close, adj_factor, value, shares, mcap, source
                       FROM prices_daily WHERE symbol_id = '183300' ORDER BY date""").fetchall()
    assert [r[6] for r in p] == ["kiwoom"] * 4 and [r[1] for r in p] == [54400, 54400, 27700, 25400]
    assert p[0][3] == 123456789 and p[0][4] == 1000                    # marcap 거래대금·주식수 유지
    assert [r[4] for r in p] == [1000, 1000, 1000, 2500]               # 직전 값 → 스냅샷
    assert p[3][5] == 25400 * 2500
    assert p[0][2] == pytest.approx(21800 / 54400) and p[3][2] == 1
    assert con.execute("SELECT count(*) FROM prices_daily WHERE symbol_id = '999990'").fetchone()[0] == 1
    before = [r[2] for r in p]                                         # 기준가 연쇄로 다시 계산해도 같은 수정계수
    recompute_adj_factor(con, ["183300"])
    after = [r[0] for r in con.execute("SELECT adj_factor FROM prices_daily WHERE symbol_id='183300' ORDER BY date").fetchall()]
    assert after == pytest.approx(before)


def test_adj_view():
    con = duckdb.connect()
    con.execute(DDL)
    assert upsert(con, parse_rows(ROWS, "183300", "KRX", "2026-08-01")) == 4
    f = dict(con.execute("SELECT strftime(date, '%Y%m%d'), adj_factor FROM v_prices_kiwoom_adj").fetchall())
    assert f["20260824"] == 1 and f["20260821"] == 1
    assert f["20260820"] == pytest.approx(21800 / 54400)
    assert f["20260819"] == pytest.approx(21800 / 54400)


def test_adj_ignores_ref_noise():
    """upd_rt 가 없는 날은 기준가가 전일 종가와 달라도(2026-09-15 이후 키움) 수정하지 않는다.
    기준가가 어긋난 이벤트일은 upd_rt 비율을 쓴다."""
    rows = [dict(r, upd_rt="") for r in ROWS]
    rows[0]["pred_pre"] = "-1000"                         # 08-24 기준가 26400 ≠ 전일 종가 27700
    rows[1].update(upd_rt="-59.93", pred_pre="+5900", cur_prc="27740")   # 기준가 21840: upd_rt 와 0.1% 차이
    con = duckdb.connect()
    con.execute(DDL)
    upsert(con, parse_rows(rows, "183300", "KRX", "2026-08-01"))
    f = dict(con.execute("SELECT strftime(date, '%Y%m%d'), adj_factor FROM v_prices_kiwoom_adj").fetchall())
    assert f["20260821"] == 1
    assert f["20260820"] == pytest.approx(1 - 0.5993)
