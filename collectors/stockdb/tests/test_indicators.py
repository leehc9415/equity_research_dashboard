"""지표 엔진 테스트 (네트워크 없이 실행): 종합 RS 의 1년 미만 처리, MTT, 150일선, 거래대금 20일 평균.

    python -m pytest -q tests/test_indicators.py
"""
import duckdb
import pandas as pd
import pytest

from src.indicators.build import build_daily, build_rs


@pytest.fixture()
def con():
    con = duckdb.connect()
    days = pd.bdate_range("2025-01-01", periods=260)
    rows = []
    for k, g in enumerate([0.002, 0.003, 0.004, 0.005]):          # 꾸준히 오르는 4종목, 기울기만 다름
        for t, d in enumerate(days):
            c = 1000 * (1 + g) ** t
            rows.append((f"0000{k + 1}0", d.date(), c, c, c, c * 10, 10.0, 1.0))
    for t, d in enumerate(days[-30:]):                             # 상장 30일 종목 → 종합 RS 점수 없음
        rows.append(("000090", d.date(), 500.0, 500.0, 500.0, 5000.0, 10.0, 1.0))
    con.execute("CREATE TABLE px (symbol_id TEXT, date DATE, close DOUBLE, high DOUBLE, low DOUBLE, "
                "value DOUBLE, volume DOUBLE, adj_factor DOUBLE)")
    con.executemany("INSERT INTO px VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    con.execute("CREATE TABLE symbols AS SELECT DISTINCT symbol_id AS id, 'KR' market, 'stock' asset_class, "
                "'KOSPI' subclass, 'x' AS name FROM px")
    build_daily(con, src="px", out="ind")
    build_rs(con, src="px", out="rs", where="TRUE", ind="ind")
    con.execute("CREATE TEMP TABLE last AS SELECT * FROM rs WHERE date = (SELECT max(date) FROM rs)")
    return con


def test_rs_unscored_ranked_on_top_but_shown_zero(con):
    rs = dict(con.execute("SELECT symbol_id, rs FROM last").fetchall())
    # 5종목 중 점수 없는 1종목이 맨 위(5위) → 점수 있는 4종목은 1~4위: 20·40·60·80
    assert [rs[f"0000{k + 1}0"] for k in range(4)] == [20, 40, 60, 80]
    assert rs["000090"] == 0


def test_mtt(con):
    px = dict(con.execute("SELECT symbol_id, mtt_px FROM ind WHERE date = (SELECT max(date) FROM ind)").fetchall())
    assert all(px[f"0000{k + 1}0"] for k in range(4)) and not px["000090"]       # 30일 종목은 150·200일선 없음
    mtt = dict(con.execute("SELECT symbol_id, mtt FROM last").fetchall())
    assert [mtt[f"0000{k + 1}0"] for k in range(4)] == [False, False, False, True]  # RS ≥ 70 은 80인 종목만


def test_ma150_and_value_ma20(con):
    r = con.execute("""SELECT ma150, value_ma20 FROM ind WHERE symbol_id = '000010'
                       ORDER BY date DESC LIMIT 1""").fetchone()
    closes = [1000 * 1.002 ** t for t in range(260)]
    assert r[0] == pytest.approx(sum(closes[-150:]) / 150)
    assert r[1] == pytest.approx(sum(closes[-20:]) * 10 / 20)
    assert con.execute("SELECT value_ma20 FROM ind WHERE symbol_id = '000090' "
                       "ORDER BY date LIMIT 1 OFFSET 18").fetchone()[0] is None    # 19일째: 아직 NULL
