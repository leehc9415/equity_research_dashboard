"""DB 카탈로그: 종목 마스터 보강, 테이블·컬럼 설명, 통합 조회 뷰, 데이터 사전.

다른 프로젝트에서 이 DB를 쓸 때는 아래 세 뷰만 알면 된다.
    v_daily     일봉: 종목정보 + 수정 OHLCV + 거래대금·시총 + 일봉 지표
    v_weekly    주봉: 종목정보 + 수정 OHLCV + 주봉 이동평균
    v_monthly   월봉: 종목정보 + 수정 OHLCV + 월봉 이동평균

    python -m src.catalog              # 마스터 보강 + 설명 + 뷰 + docs/DATA_DICTIONARY.md
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .prices.db import connect
from .universe.config import DATA_DIR, ROOT

DOC_PATH = ROOT / "docs" / "DATA_DICTIONARY.md"

# --------------------------------------------------------------------------- 설명
TABLES = {
    "symbols": "종목·지수·ETF·선물·환율·금리·크립토 마스터. id 가 모든 테이블의 symbol_id 와 연결된다.",
    "group_meta": "대시보드 화면 그룹 설정(정렬, 벤치마크, 카드 하단 지표).",
    "groups": "화면 그룹 ↔ 심볼 소속과 기본 표시 순서.",
    "sector_defs": "한국 자체 섹터 21개 정의(포함 네이버 업종).",
    "sector_map": "한국 종목 → 자체 섹터 매핑(편입 여부·제외 사유 포함).",
    "prices_daily": "원천 일봉. 원주가 + 수정계수(adj_factor). 수정주가 = 원주가 × adj_factor.",
    "ind_daily": "일봉 지표(수정주가 기준). 매 실행 시 전체 재생성.",
    "bars_weekly": "주봉(ISO 주, 월~일) OHLCV + 주봉 이동평균. 수정주가 기준.",
    "bars_monthly": "월봉 OHLCV + 월봉 이동평균. 수정주가 기준.",
    "collect_log": "수집 실행 기록(작업, 상태, 행 수, 날짜 범위).",
    "rs_daily": "한국 코스피·코스닥 보통주 RS(IBD 방식). 날짜별 전 종목 백분위 0~99. 매 실행 시 전체 재생성.",
}

COLUMNS = {
    "symbols": {
        "id": "심볼 ID. 한국 종목 6자리 코드, 지수 KS11/KQ11, 해외는 티커 또는 별칭(WTI 등), 한국 섹터 KRSEC:<id>",
        "market": "KR / US / GLOBAL", "asset_class": "stock / etf / index / future / fx / rate / crypto / sector_index",
        "category": "미국 ETF 분류: index / sector / industry / theme / country",
        "subclass": "매크로 세부: energy / precious / base / agri / fx / rate / vol / bond / crypto",
        "name": "표시 이름", "currency": "통화", "unit": "price 또는 yield_pct(금리)",
        "source": "1차 시세 소스", "ticker": "소스에서 쓰는 티커",
        "fallback_source": "백업 소스", "fallback_ticker": "백업 소스 티커",
        "note": "메모", "inferred": "원형에서 확인 못 해 추정으로 넣은 심볼",
        "active": "수집 대상 여부",
        "origin": "등록 경로: kr_sectors / us_etf / macro / kr_sector_map(유니버스 종목) / krx_all(그 외 한국 종목)",
    },
    "prices_daily": {
        "symbol_id": "심볼 ID", "date": "거래일",
        "open": "시가(원주가)", "high": "고가(원주가)", "low": "저가(원주가)", "close": "종가(원주가, KRX 정규장)",
        "ref_price": "한국: 기준가(전일 종가를 권리 변동만큼 조정). 수정계수 계산용",
        "adj_factor": "수정계수. 수정주가 = 원주가 × adj_factor (최신일 = 1)",
        "volume": "거래량(원주 기준)", "value": "거래대금(현지 통화)",
        "value_is_est": "TRUE면 종가×거래량 추정치(미국)", "mcap": "시가총액(원, 한국 종목)",
        "shares": "상장주식수(한국 종목)",
        "source": "marcap(~2026-09-11) / krx(2026-09-14~ KRX 정규장 확정 시세, pykrx) / naver(일별 임시값, 시간외 포함 → KRX 로 교체) / naver_fix(옛 보정) / fdr(지수) / yf / sector_idx(한국 섹터 지수: close=지수, volume=구성 종목 수, mcap=구성 시총 합)",
        "fetched_at": "수집 시각",
    },
    "ind_daily": {
        "ma5": "5일 이동평균(수정종가)", "ma10": "10일 이동평균", "ma20": "20일 이동평균",
        "ma50": "50일 이동평균", "ma60": "60일 이동평균", "ma120": "120일 이동평균",
        "ma200": "200일 이동평균", "vol_ma50": "수정 거래량 50일 평균",
        "high_52w": "최근 365일 수정 고가 최대", "low_52w": "최근 365일 수정 저가 최소",
        "ret_1m": "1개월 수익률(달력 기준, 소수)", "ret_3m": "3개월 수익률",
        "ret_6m": "6개월 수익률", "ret_12m": "12개월 수익률",
    },
    "rs_daily": {
        "rs_score": "IBD 점수 = 2×(P0/P63) + P63/P126 + P126/P189 + P189/P252 (Pn: n거래일 전 수정종가)",
        "rs": "종합 RS(0~99): rs_score 의 날짜별 백분위. 252거래일 이력 없으면 0",
        "rs_1m": "21거래일 수익률 백분위(0~99). 상장 기간이 짧으면 상장 후 첫 종가 기준",
        "rs_3m": "63거래일 수익률 백분위", "rs_6m": "126거래일 수익률 백분위",
        "rs_12m": "252거래일 수익률 백분위",
    },
    "bars_weekly": {
        "period_start": "주 시작일(월요일)", "date": "그 주 마지막 거래일", "n_days": "그 주 거래일 수",
        "volume": "수정 거래량 합", "value": "거래대금 합", "ma10": "10주 이동평균",
        "ma30": "30주 이동평균", "ma50": "50주 이동평균", "vol_ma10": "주간 거래량 10주 평균",
        "is_complete": "FALSE = 진행 중인 이번 주",
    },
    "bars_monthly": {
        "period_start": "월 시작일", "date": "그 달 마지막 거래일", "n_days": "그 달 거래일 수",
        "ma10": "10개월 이동평균", "ma12": "12개월 이동평균", "is_complete": "FALSE = 진행 중인 이번 달",
    },
}

VIEWS = {
    "v_daily": "일봉 통합 조회: 종목정보 + 수정 OHLCV + 거래대금·시총 + 일봉 지표 + 한국 섹터",
    "v_weekly": "주봉 통합 조회: 종목정보 + 수정 OHLCV + 주봉 지표",
    "v_monthly": "월봉 통합 조회: 종목정보 + 수정 OHLCV + 월봉 지표",
    "v_prices_adj": "수정주가 뷰(원천 prices_daily × adj_factor)",
    "v_rs_universe": "RS 계산 대상: 코스피·코스닥 보통주(우선주·스팩·코넥스 제외, 리츠 포함)",
    "v_symbols": "종목 마스터 + 한국 섹터 + 시세 보유 기간",
}

VIEW_SQL = """
CREATE OR REPLACE VIEW v_symbols AS
SELECT s.*, m.sector_id, m.sector_name, m.upjong AS naver_upjong,
       coalesce(m.included, FALSE) AS in_kr_universe,
       p.first_date, p.last_date, p.n_days
FROM symbols s
LEFT JOIN sector_map m ON m.code = s.id
LEFT JOIN (SELECT symbol_id, min(date) first_date, max(date) last_date, count(*) n_days
           FROM prices_daily GROUP BY 1) p ON p.symbol_id = s.id;

CREATE OR REPLACE VIEW v_daily AS
SELECT p.symbol_id, s.name, s.market, s.asset_class, m.sector_name,
       p.date, p.open, p.high, p.low, p.close, p.close_raw, p.adj_factor,
       p.volume, p.value, p.mcap,
       i.ma5, i.ma10, i.ma20, i.ma50, i.ma60, i.ma120, i.ma200,
       i.vol_ma50, i.high_52w, i.low_52w,
       p.close / i.high_52w - 1 AS dd_52w,
       i.ret_1m, i.ret_3m, i.ret_6m, i.ret_12m,
       r.rs, r.rs_1m, r.rs_3m, r.rs_6m, r.rs_12m
FROM v_prices_adj p
LEFT JOIN ind_daily i USING (symbol_id, date)
LEFT JOIN rs_daily r USING (symbol_id, date)
LEFT JOIN symbols s ON s.id = p.symbol_id
LEFT JOIN sector_map m ON m.code = p.symbol_id;

CREATE OR REPLACE VIEW v_weekly AS
SELECT b.symbol_id, s.name, s.market, s.asset_class, b.* EXCLUDE (symbol_id)
FROM bars_weekly b LEFT JOIN symbols s ON s.id = b.symbol_id;

CREATE OR REPLACE VIEW v_monthly AS
SELECT b.symbol_id, s.name, s.market, s.asset_class, b.* EXCLUDE (symbol_id)
FROM bars_monthly b LEFT JOIN symbols s ON s.id = b.symbol_id;
"""


# --------------------------------------------------------------------------- 마스터 보강
def register_all_kr(con) -> int:
    """유니버스 밖 종목·상장폐지 종목까지 이름을 등록 (origin='krx_all')."""
    frames = []
    for f in sorted((DATA_DIR / "raw" / "marcap").glob("marcap-*.parquet")):
        d = pd.read_parquet(f, columns=["Code", "Name", "Market", "Date"])
        frames.append(d.sort_values("Date").drop_duplicates("Code", keep="last"))
    lst = DATA_DIR / "universe" / "krx_listing.csv"
    if lst.exists():
        l = pd.read_csv(lst, dtype={"code": str}).rename(
            columns={"code": "Code", "name": "Name", "market": "Market"})
        l["Date"] = pd.Timestamp.today()
        frames.append(l[["Code", "Name", "Market", "Date"]])
    if not frames:
        return 0
    names = (pd.concat(frames).sort_values("Date").drop_duplicates("Code", keep="last"))
    names["Code"] = names["Code"].astype(str).str.zfill(6)
    names["Market"] = names["Market"].str.replace(" GLOBAL", "", regex=False)
    con.register("_n", names)
    con.execute("DELETE FROM symbols WHERE origin = 'krx_all'")
    n = con.execute("""
        INSERT INTO symbols (id, market, asset_class, subclass, name, currency, unit, source,
                             ticker, active, origin)
        SELECT n.Code, 'KR', 'stock', n.Market, n.Name, 'KRW', 'price', 'naver', n.Code,
               TRUE, 'krx_all'
        FROM _n n
        WHERE n.Code NOT IN (SELECT id FROM symbols)
          AND n.Code IN (SELECT DISTINCT symbol_id FROM prices_daily)""").fetchone()[0]
    # 유니버스 종목(kr_sector_map)도 시장 구분을 subclass 에 채움
    con.execute("""UPDATE symbols s SET subclass = n.Market FROM _n n
                   WHERE s.id = n.Code AND s.market = 'KR' AND s.asset_class = 'stock'""")
    con.unregister("_n")
    return n


def _q(s: str) -> str:
    return s.replace("'", "''")


def apply_comments(con):
    existing = {r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
    for t, desc in TABLES.items():
        if t in existing:
            con.execute(f"COMMENT ON TABLE {t} IS '{_q(desc)}'")
    for v, desc in VIEWS.items():
        con.execute(f"COMMENT ON VIEW {v} IS '{_q(desc)}'")
    for t, cols in COLUMNS.items():
        if t not in existing:
            continue
        have = {r[0] for r in con.execute(
            "SELECT column_name FROM duckdb_columns() WHERE table_name = ?", [t]).fetchall()}
        for c, desc in cols.items():
            if c in have:
                con.execute(f"COMMENT ON COLUMN {t}.{c} IS '{_q(desc)}'")


def write_dictionary(con, path: Path = DOC_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    L = ["# market.duckdb 데이터 사전", "",
         "자동 생성 문서 (`python -m src.catalog`). 테이블·컬럼 설명은 DB 안에도 들어 있어서",
         "`SELECT * FROM duckdb_columns() WHERE table_name='v_daily'` 로 볼 수 있다.", "",
         "## 빠른 시작", "", "```python", "import duckdb",
         "con = duckdb.connect('data/market.duckdb', read_only=True)",
         "# 삼성전자 최근 1년 일봉 + 지표",
         "con.sql(\"SELECT * FROM v_daily WHERE symbol_id='005930' AND date >= current_date - 365\").df()",
         "# 반도체 섹터 종목의 최근일 52주 고점 대비",
         "con.sql(\"SELECT name, close, dd_52w FROM v_daily WHERE sector_name='반도체' \"",
         "        \"AND date = (SELECT max(date) FROM v_daily WHERE market='KR')\").df()",
         "# 미국 섹터 ETF 주봉",
         "con.sql(\"SELECT * FROM v_weekly WHERE symbol_id IN ('XLK','SMH')\").df()",
         "```", "", "## 현황", ""]
    cov = con.execute("""
        SELECT s.market, s.asset_class, count(DISTINCT p.symbol_id) n_symbols,
               min(p.date) first_date, max(p.date) last_date, count(*) n_rows
        FROM prices_daily p LEFT JOIN symbols s ON s.id = p.symbol_id
        GROUP BY 1, 2 ORDER BY 1, 2""").df()
    L += ["| " + " | ".join(cov.columns) + " |", "|" + "---|" * len(cov.columns)]
    L += ["| " + " | ".join(str(v) for v in r) + " |" for r in cov.itertuples(index=False)]
    L += ["", "## 원칙", "",
          "- 가격 컬럼(open/high/low/close)은 뷰에서 모두 **수정주가**. 원주가는 `close_raw`, 원천 테이블 `prices_daily`.",
          "- 한국 종가는 KRX 정규장 종가(HTS와 동일). NXT 시간외 가격은 쓰지 않는다.",
          "- 이동평균은 거래일 N개 기준, 52주 고저·기간 수익률은 달력 기준.",
          "- 지표 테이블은 파생물이라 매 실행 시 전체 재생성된다. 직접 수정하지 말 것.", ""]
    objs = con.execute("""
        SELECT view_name, comment, 'VIEW' kind FROM duckdb_views() WHERE NOT internal AND schema_name = 'main'
        UNION ALL SELECT table_name, comment, 'TABLE' FROM duckdb_tables() WHERE NOT temporary
        ORDER BY 3 DESC, 1""").fetchall()
    for name, comment, kind in objs:
        if name.startswith("_"):
            continue
        L += [f"## {name} ({kind})", "", comment or "", ""]
        cols = con.execute("""SELECT column_name, data_type, comment FROM duckdb_columns()
                              WHERE table_name = ? ORDER BY column_index""", [name]).fetchall()
        L += ["| 컬럼 | 타입 | 설명 |", "|---|---|---|"]
        L += [f"| {c} | {t} | {d or ''} |" for c, t, d in cols]
        L.append("")
    path.write_text("\n".join(L), encoding="utf-8")


def build_catalog(con=None):
    own = con is None
    con = con or connect()
    n = register_all_kr(con)
    con.execute(VIEW_SQL)
    apply_comments(con)
    write_dictionary(con)
    print(f"  카탈로그: 한국 종목 마스터 {n}개 추가 등록, 뷰·설명 갱신, {DOC_PATH.name} 생성")
    if own:
        con.execute("CHECKPOINT")
        con.close()


if __name__ == "__main__":
    build_catalog()
