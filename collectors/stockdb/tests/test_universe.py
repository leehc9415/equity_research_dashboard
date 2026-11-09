"""유니버스 모듈 테스트 (네트워크 없이 실행).

    python -m pytest -q tests/test_universe.py
"""
import pandas as pd
import duckdb

from src.universe.config import load_universe, norm_upjong
from src.universe.naver_upjong import parse_upjong_detail, parse_upjong_list
from src.universe.build_kr_sector_map import build
from src.universe.load_universe import load

# m.stock.naver.com/api/stocks/industry 응답 축약본
LIST_JSON = {"stockListSortType": "INDUSTRY", "groups": [
    {"no": 278, "name": "반도체와반도체장비", "totalCount": 173},
    {"no": 263, "name": "게임엔터테인먼트", "totalCount": 33},
    {"no": 25, "name": "기타", "totalCount": 10},
]}
DETAIL_JSON = {"stockListSortType": "INDUSTRY", "totalCount": 3, "stocks": [
    {"itemCode": "005930", "stockName": "삼성전자", "sosok": "0", "marketValue": "13,446,440",
     "stockEndType": "stock"},
    {"itemCode": "396470", "stockName": "워트", "sosok": "1", "marketValue": "1,267",
     "stockEndType": "stock"},
    {"itemCode": "091160", "stockName": "KODEX 반도체", "sosok": "0", "marketValue": "N/A",
     "stockEndType": "etf"},
]}


def test_parse_list():
    assert parse_upjong_list(LIST_JSON) == [(278, "반도체와반도체장비", 173),
                                            (263, "게임엔터테인먼트", 33), (25, "기타", 10)]


def test_parse_detail():
    got = parse_upjong_detail(DETAIL_JSON)
    assert [g["code"] for g in got] == ["005930", "396470", "091160"]
    assert got[0]["mcap_100m"] == 13_446_440 and got[0]["sosok"] == "KOSPI"
    assert got[1]["sosok"] == "KOSDAQ" and got[2]["mcap_100m"] is None
    assert got[2]["end_type"] == "etf"


def test_norm_upjong():
    assert norm_upjong("섬유, 의류, 신발, 호화품") == norm_upjong("섬유,의류,신발,호화품")
    assert norm_upjong("호텔,레스토랑,레저") == "호텔레스토랑레저"


def test_config_valid():
    uni = load_universe()
    assert uni.errors == []
    assert len(uni.kr["sectors"]) == 21
    assert len(uni.us["pinned"]) + len(uni.us["symbols"]) == 36
    tickers = {m["ticker"] for m in uni.macro["symbols"]}
    assert {"CL=F", "KRW=X", "^TNX", "2YY=F", "BTC-USD", "DX-Y.NYB"} <= tickers


def test_build_sector_map():
    uni = load_universe()
    members = pd.DataFrame([
        dict(upjong_no=278, upjong="반도체와반도체장비", code="005930", name="삼성전자"),
        dict(upjong_no=278, upjong="반도체와반도체장비", code="005935", name="삼성전자우"),
        dict(upjong_no=278, upjong="반도체와반도체장비", code="000010", name="소형반도체"),
        dict(upjong_no=263, upjong="게임엔터테인먼트", code="259960", name="크래프톤"),
        dict(upjong_no=500, upjong="신규업종이름", code="111110", name="신규"),
        dict(upjong_no=999, upjong="기타", code="222220", name="기타사"),
        dict(upjong_no=263, upjong="게임엔터테인먼트", code="333330", name="게임스팩1호"),
    ]).assign(fetched_at="2026-11-02T20:00:00")
    listing = pd.DataFrame([
        dict(code="005930", name="삼성전자", market="KOSPI", mcap_100m=3_000_000, shares=1),
        dict(code="005935", name="삼성전자우", market="KOSPI", mcap_100m=300_000, shares=1),
        dict(code="000010", name="소형반도체", market="KOSDAQ", mcap_100m=150, shares=1),
        dict(code="259960", name="크래프톤", market="KOSPI", mcap_100m=83_246, shares=1),
        dict(code="111110", name="신규", market="KOSDAQ", mcap_100m=1_000, shares=1),
        dict(code="222220", name="기타사", market="KOSDAQ", mcap_100m=1_000, shares=1),
        dict(code="333330", name="게임스팩1호", market="KOSDAQ", mcap_100m=500, shares=1),
    ])
    df, report = build(uni, members, listing)
    r = df.set_index("code")["exclude_reason"].to_dict()
    assert r["005930"] == "" and r["259960"] == ""
    assert r["005935"] == ""  # 원형처럼 우선주 포함
    assert r["000010"].startswith("시총 300억")
    assert r["111110"] == "미매핑 업종"
    assert r["222220"] == "무시 업종"
    assert r["333330"] == "스팩"
    assert "신규업종이름: 1종목" in report
    assert df.set_index("code").loc["005930", "sector_id"] == "semis"


def test_load_duckdb(tmp_path):
    uni = load_universe()
    sm = pd.DataFrame([dict(code="005930", name="삼성전자", market="KOSPI",
                            mcap_100m=3_000_000, upjong_no=278, upjong="반도체와반도체장비",
                            sector_id="semis", sector_name="반도체", source="naver_upjong",
                            included=True, exclude_reason="")])
    csv = tmp_path / "map.csv"
    sm.to_csv(csv, index=False)
    db = tmp_path / "t.duckdb"
    load(uni, db, csv)
    res = load(uni, db, csv)  # 두 번 실행해도 같아야 함
    groups = dict((g, n) for g, n, _ in res["groups"])
    assert groups == {"KR_SECTOR": 23, "US_SECTOR_ETF": 36, "MACRO": len(uni.macro["symbols"])}
    con = duckdb.connect(str(db))
    assert con.execute("SELECT fallback_ticker FROM symbols WHERE id='005930'").fetchone()[0] \
        == "005930.KS"
    assert con.execute("SELECT count(*) FROM symbols WHERE id='KRSEC:semis'").fetchone()[0] == 1


def test_indicators_and_rs(tmp_path):
    """지표·RS 엔진: SQL 결과를 pandas 기준 계산과 대조 (합성 데이터)."""
    import numpy as np
    from src.prices import db as pdb
    from src.universe.load_universe import DDL as UDDL
    from src.indicators.build import build_all
    from src import catalog
    con = pdb.connect(tmp_path / "t.duckdb")
    con.execute(UDDL)
    dates = pd.bdate_range("2025-01-01", "2026-11-02")
    rng = np.random.default_rng(1)
    rows, syms = [], []
    for i in range(60):
        code = f"{i:05d}0"
        start = 0 if i % 10 else 300                     # 10%는 늦게 상장
        px = 1000 * np.cumprod(1 + rng.normal(0, 0.02, len(dates)))[start:]
        rows.append(pd.DataFrame({"symbol_id": code, "date": dates[start:].date, "open": px,
                                  "high": px * 1.01, "low": px * 0.99, "close": px,
                                  "adj_factor": 1.0, "volume": 100.0, "value": px * 100,
                                  "source": "marcap"}))
        syms.append((code, "KR", "stock", "KOSPI", f"종목{i}", "krx_all"))
    pdb.upsert_prices(con, pd.concat(rows))
    con.executemany("INSERT INTO symbols (id, market, asset_class, subclass, name, origin) "
                    "VALUES (?,?,?,?,?,?)", syms)
    build_all(con, verbose=False)
    con.execute(catalog.VIEW_SQL)
    catalog.apply_comments(con)
    d = con.execute("SELECT * FROM v_daily WHERE symbol_id='000010' ORDER BY date").df()
    assert (d["ma200"] - d["close"].rolling(200).mean()).abs().max() < 1e-9
    assert (d["vol_ma50"].dropna() == 100).all()
    last = d.iloc[-1]
    ref = d[pd.to_datetime(d["date"]) <= pd.Timestamp(last["date"]) - pd.DateOffset(months=3)].iloc[-1]
    assert abs(last["ret_3m"] - (last["close"] / ref["close"] - 1)) < 1e-12
    rs = con.execute("SELECT * FROM rs_daily WHERE date='2026-11-02'").df()
    assert len(rs) == 60 and rs["rs"].between(0, 99).all()
    assert (rs.loc[rs["rs_score"].isna(), "rs"] == 0).all()          # 1년 미만 = 0
    w = con.execute("SELECT * FROM v_weekly WHERE symbol_id='000010' ORDER BY period_start").df()
    last_w = w.iloc[-1]                                              # 마지막 주는 이번 주가 지나야 완료
    this_week = con.execute("SELECT date_trunc('week', current_date)").fetchone()[0]
    assert bool(last_w["is_complete"]) == (this_week > last_w["period_start"]) and w["ma30"].notna().any()


def test_sector_index(tmp_path):
    """섹터 지수 = 전일 시총가중 수익률 누적. 액면분할에도 지수가 튀지 않아야 함."""
    from src.prices import db as pdb
    from src.universe.load_universe import DDL as UDDL
    from src.indicators.sector_index import build_sector_index
    con = pdb.connect(tmp_path / "t.duckdb")
    con.execute(UDDL)
    d = pd.bdate_range("2026-01-01", periods=4).date
    # A: 100→110→110→(5:1 분할, 원주가 22, adj_factor 이전 0.2)  시총 1000억
    # B: 200→200→190→190                                         시총 3000억
    rows = [("A", d[0], 100, 0.2, 1e11), ("A", d[1], 110, 0.2, 1.1e11), ("A", d[2], 110, 0.2, 1.1e11),
            ("A", d[3], 22, 1.0, 1.1e11),
            ("B", d[0], 200, 1.0, 3e11), ("B", d[1], 200, 1.0, 3e11), ("B", d[2], 190, 1.0, 2.85e11),
            ("B", d[3], 190, 1.0, 2.85e11)]
    pdb.upsert_prices(con, pd.DataFrame(rows, columns=["symbol_id", "date", "close", "adj_factor", "mcap"])
                      .assign(source="marcap", open=lambda x: x.close, high=lambda x: x.close,
                              low=lambda x: x.close))
    con.execute("INSERT INTO sector_map (code, sector_id, included) VALUES ('A','x',TRUE),('B','x',TRUE)")
    build_sector_index(con, verbose=False)
    idx = con.execute("SELECT close FROM prices_daily WHERE symbol_id='KRSEC:x' ORDER BY date").fetchall()
    exp = [100, 100 * (1 + 0.25 * 0.10), None, None]
    exp[2] = exp[1] * (1 + (1.1e11 * 0 + 3e11 * (190 / 200 - 1)) / 4.1e11)
    exp[3] = exp[2]                                               # 분할일: 수익률 0
    assert all(abs(a[0] - b) < 1e-9 for a, b in zip(idx, exp))


def test_build_daily_parts(tmp_path):
    """묶음 계산(part/nparts) 결과가 한 번에 계산한 것과 같아야 함."""
    import numpy as np
    from src.prices import db as pdb
    from src.indicators.build import build_daily
    con = pdb.connect(tmp_path / "t.duckdb")
    dates = pd.bdate_range("2025-01-01", periods=300).date
    rows = [pd.DataFrame({"symbol_id": f"S{i}", "date": dates, "open": 1.0, "high": 1.0, "low": 1.0,
                          "close": np.arange(300) + i + 1.0, "adj_factor": 1.0, "volume": 10.0,
                          "source": "marcap"}) for i in range(7)]
    pdb.upsert_prices(con, pd.concat(rows))
    build_daily(con)
    a = con.execute("SELECT * FROM ind_daily ORDER BY symbol_id, date").df()
    for p in range(3):
        build_daily(con, part=p, nparts=3)
    b = con.execute("SELECT * FROM ind_daily ORDER BY symbol_id, date").df()
    pd.testing.assert_frame_equal(a, b)
