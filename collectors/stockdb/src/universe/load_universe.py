"""유니버스 설정을 DuckDB(data/market.duckdb)에 적재한다.

만드는 테이블
  symbols        모든 심볼의 마스터 (지수·ETF·선물·환율·금리·크립토·한국 종목·섹터 지수)
  group_meta     화면 그룹 설정 (정렬 방식, 벤치마크, 카드 하단 지표, 링크)
  groups         그룹 ↔ 심볼 소속 (화면에 무엇이 어떤 순서로 나오는지)
  sector_defs    한국 자체 섹터 정의 (포함 네이버 업종, 원형 종목 수)
  sector_map     한국 종목 → 섹터 (편입 여부·제외 사유 포함)

여러 번 실행해도 결과가 같다 (설정에서 온 행은 지우고 다시 넣음).

실행:
    python -m src.universe.load_universe
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import pandas as pd

from .config import DATA_DIR, Universe, load_universe

DB_PATH = DATA_DIR / "market.duckdb"
SECTOR_MAP_CSV = DATA_DIR / "universe" / "kr_sector_map.csv"

DDL = """
CREATE TABLE IF NOT EXISTS symbols (
  id              TEXT PRIMARY KEY,
  market          TEXT,      -- KR / US / GLOBAL
  asset_class     TEXT,      -- stock / etf / index / future / fx / rate / crypto / sector_index
  category        TEXT,      -- index / sector / industry / theme / country (US ETF)
  subclass        TEXT,      -- energy / precious / base / agri / fx / rate / vol / bond ...
  name            TEXT,
  currency        TEXT,
  unit            TEXT,      -- price / yield_pct
  source          TEXT,      -- yf / fdr / derived
  ticker          TEXT,      -- 소스에서 쓰는 티커
  fallback_source TEXT,
  fallback_ticker TEXT,
  note            TEXT,
  inferred        BOOLEAN DEFAULT FALSE,  -- 원형에서 확인 못 하고 추정해 넣은 항목
  active          BOOLEAN DEFAULT TRUE,
  origin          TEXT       -- 어느 설정에서 왔는지 (재적재 시 이 값으로 삭제)
);
CREATE TABLE IF NOT EXISTS group_meta (
  group_id      TEXT PRIMARY KEY,
  title         TEXT,
  market        TEXT,
  benchmark     TEXT,
  sort          TEXT,        -- rel_3m_desc / fixed
  card_footer   TEXT,        -- JSON 배열
  pinned_footer TEXT,
  link_template TEXT
);
CREATE TABLE IF NOT EXISTS groups (
  group_id     TEXT,
  symbol_id    TEXT,
  display_name TEXT,
  sort_order   INTEGER,
  pinned       BOOLEAN,
  PRIMARY KEY (group_id, symbol_id)
);
CREATE TABLE IF NOT EXISTS sector_defs (
  sector_id    TEXT PRIMARY KEY,
  name         TEXT,
  naver_upjong TEXT,         -- JSON 배열
  ref_count    INTEGER,
  sort_order   INTEGER
);
CREATE TABLE IF NOT EXISTS sector_map (
  code           TEXT PRIMARY KEY,
  name           TEXT,
  market         TEXT,
  mcap_100m      DOUBLE,     -- 맵 생성 시점 시총(억원). 일별 시총은 시세 테이블에서
  upjong_no      TEXT,      -- 네이버 업종번호 또는 WICS 업종코드(G203020)
  upjong         TEXT,
  sector_id      TEXT,
  sector_name    TEXT,
  source         TEXT,       -- naver_upjong / override
  included       BOOLEAN,
  exclude_reason TEXT,
  valid_from     DATE
);
"""


def _sym_rows(uni: Universe) -> list[dict]:
    rows: list[dict] = []

    # 한국: 고정 지수
    for p in uni.kr["pinned"]:
        rows.append(dict(id=p["id"], market="KR", asset_class="index", name=p["name"],
                         currency="KRW", unit="price", source=p.get("source", "fdr"),
                         ticker=p.get("ticker", p["id"]), fallback_source="yf",
                         fallback_ticker=p.get("yf"), origin="kr_sectors"))
    # 한국: 섹터 지수 (시세에서 계산하는 파생 심볼)
    for s in uni.kr["sectors"]:
        rows.append(dict(id=f"KRSEC:{s['id']}", market="KR", asset_class="sector_index",
                         name=s["name"], currency="KRW", unit="price", source="derived",
                         ticker=s["id"], origin="kr_sectors"))

    # 미국 ETF
    d = uni.us.get("defaults", {})
    for p in uni.us["pinned"] + uni.us["symbols"]:
        rows.append(dict(id=p["id"], market="US", asset_class=p.get("asset_class", d["asset_class"]),
                         category=p.get("category"), name=p["name"],
                         currency=p.get("currency", d["currency"]), unit="price",
                         source=p.get("source", d["source"]), ticker=p.get("ticker", p["id"]),
                         fallback_source="fdr", fallback_ticker=p.get("ticker", p["id"]),
                         origin="us_etf"))

    # 매크로
    d = uni.macro.get("defaults", {})
    for m in uni.macro["symbols"]:
        fb = m.get("fallback") or {}
        fb_src, fb_tk = next(iter(fb.items()), (None, None))
        rows.append(dict(id=m["id"], market="GLOBAL", asset_class=m["asset_class"],
                         subclass=m.get("subclass"), name=m["name"],
                         currency=m.get("currency"), unit=m.get("unit", "price"),
                         source=m.get("source", d.get("source", "yf")), ticker=m["ticker"],
                         fallback_source=fb_src, fallback_ticker=fb_tk, note=m.get("note"),
                         inferred=bool(m.get("inferred", False)), origin="macro"))
    return rows


def _group_rows(uni: Universe) -> tuple[list[dict], list[dict]]:
    metas, members = [], []

    def _meta(g: dict):
        metas.append(dict(group_id=g["id"], title=g["title"], market=g["market"],
                          benchmark=g.get("benchmark"), sort=g["sort"],
                          card_footer=json.dumps(g.get("card_footer", [])),
                          pinned_footer=json.dumps(g.get("pinned_footer", [])),
                          link_template=g.get("link_template")))

    # 한국 업종: 지수 고정 + 섹터 지수
    g = uni.kr["group"]
    _meta(g)
    order = 0
    for p in uni.kr["pinned"]:
        order += 1
        members.append(dict(group_id=g["id"], symbol_id=p["id"], display_name=p["name"],
                            sort_order=order, pinned=True))
    for s in uni.kr["sectors"]:
        order += 1
        members.append(dict(group_id=g["id"], symbol_id=f"KRSEC:{s['id']}",
                            display_name=s["name"], sort_order=order, pinned=False))

    # 미국 ETF
    g = uni.us["group"]
    _meta(g)
    for i, p in enumerate(uni.us["pinned"] + uni.us["symbols"], 1):
        members.append(dict(group_id=g["id"], symbol_id=p["id"], display_name=p["name"],
                            sort_order=i, pinned=i <= len(uni.us["pinned"])))

    # 매크로
    g = uni.macro["group"]
    _meta(g)
    for i, m in enumerate(uni.macro["symbols"], 1):
        members.append(dict(group_id=g["id"], symbol_id=m["id"], display_name=m["name"],
                            sort_order=i, pinned=False))
    return metas, members


def _insert(con, table: str, rows: list[dict] | pd.DataFrame):
    df = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    if df.empty:
        return
    cols = [c for c in con.table(table).columns if c in df.columns]
    con.register("_tmp", df[cols])
    con.execute(f"INSERT INTO {table} ({', '.join(cols)}) SELECT {', '.join(cols)} FROM _tmp")
    con.unregister("_tmp")


def load(uni: Universe, db_path: Path = DB_PATH, sector_map_csv: Path = SECTOR_MAP_CSV) -> dict:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute(DDL)
    con.execute("BEGIN")
    try:
        # 설정에서 온 행만 지우고 다시 넣는다 (한국 개별 종목은 아래에서 따로)
        con.execute("DELETE FROM symbols WHERE origin IN ('kr_sectors','us_etf','macro')")
        _insert(con, "symbols", _sym_rows(uni))

        metas, members = _group_rows(uni)
        gids = [m["group_id"] for m in metas]
        con.execute("DELETE FROM groups WHERE group_id IN ?", [gids])
        con.execute("DELETE FROM group_meta WHERE group_id IN ?", [gids])
        _insert(con, "group_meta", metas)
        _insert(con, "groups", members)

        con.execute("DELETE FROM sector_defs")
        _insert(con, "sector_defs", [
            dict(sector_id=s["id"], name=s["name"],
                 naver_upjong=json.dumps(s["naver_upjong"], ensure_ascii=False),
                 ref_count=s.get("ref_count"), sort_order=i)
            for i, s in enumerate(uni.kr["sectors"], 1)])

        n_map = 0
        if sector_map_csv.exists():
            sm = pd.read_csv(sector_map_csv, dtype={"code": str})
            sm["valid_from"] = pd.Timestamp.today().normalize()
            con.execute("DELETE FROM sector_map")
            _insert(con, "sector_map", sm)
            n_map = int(sm["included"].sum())

            # 편입 종목을 symbols 에도 등록 (시세 수집 대상이 됨)
            stk = sm[sm["included"]]
            # 유니버스 종목은 kr_sector_map 으로 등록 (전 종목 마스터 'krx_all' 에 있던 같은 코드는 교체)
            con.execute("DELETE FROM symbols WHERE origin = 'kr_sector_map' "
                        "OR (origin = 'krx_all' AND id IN (SELECT unnest(?)))",
                        [stk["code"].tolist()])
            _insert(con, "symbols", pd.DataFrame({
                "id": stk["code"], "market": "KR", "asset_class": "stock",
                "name": stk["name"], "currency": "KRW", "unit": "price",
                "source": "naver", "ticker": stk["code"], "fallback_source": "yf",
                "fallback_ticker": stk["code"] + stk["market"].map(
                    {"KOSPI": ".KS", "KOSDAQ": ".KQ"}).fillna(".KS"),
                "origin": "kr_sector_map"}))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    summary = con.execute("""
        SELECT g.group_id, count(*) AS n, sum(CASE WHEN s.inferred THEN 1 ELSE 0 END) AS inferred
        FROM groups g JOIN symbols s ON s.id = g.symbol_id
        GROUP BY 1 ORDER BY 1""").fetchall()
    con.close()
    return {"groups": summary, "kr_stocks": n_map}


def main():
    ap = argparse.ArgumentParser(description="유니버스 → DuckDB 적재")
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--sector-map", type=Path, default=SECTOR_MAP_CSV)
    a = ap.parse_args()

    uni = load_universe()
    if uni.errors:
        raise SystemExit("설정 오류:\n  " + "\n  ".join(uni.errors))
    res = load(uni, a.db, a.sector_map)
    print(f"DB: {a.db}")
    for gid, n, inf in res["groups"]:
        print(f"  {gid:<14} {n:>3}개" + (f" (추정 {inf}개)" if inf else ""))
    if res["kr_stocks"]:
        print(f"  한국 섹터 편입 종목 {res['kr_stocks']}개 → symbols 등록")
    else:
        print("  (kr_sector_map.csv 없음 → 한국 종목은 아직 미적재. "
              "naver_upjong → build_kr_sector_map 먼저 실행)")


if __name__ == "__main__":
    main()
