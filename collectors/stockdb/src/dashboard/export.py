"""대시보드 데이터 내보내기: DuckDB → site/data/*.js

site/index.html 을 브라우저로 바로 열면(file://) 동작하도록 JSON 대신 <script> 로 읽는 .js 파일로 쓴다.
GitHub Pages 같은 정적 호스팅에도 그대로 올라간다.

  site/data/overview.js       화면 그룹 3개(한국 업종 / 미국 지수·섹터 ETF / 상품·통화·금리)의 카드
  site/data/kr_<섹터id>.js     섹터 클릭 시 불러오는 구성 종목 카드
  site/data/rs.js             RS 랭킹 탭: 섹터 RRG + 종목 RS 표 (rs_export.py)

카드 한 장
  chg_1d   당일 등락            ret_1m  1개월 수익률        rel_3m  3개월 상대수익률(벤치마크 대비 %p)
  dd_52w   52주 고점 대비       rs      종합 RS(한국 종목)
  badges   일·주·월 추세 뱃지 = 종가가 이동평균 위(1)/아래(0)
             일 = 50일선, 주 = 30주선, 월 = 10개월선  (와인스타인·오닐식 장기 추세 기준)
  차트 주황선 = 일봉 120일선 / 주봉 30주선 / 월봉 10개월선 (LINE_MA)
  changed  뱃지 3개 중 하나라도 전 거래일과 방향이 바뀌면 True → 주황 테두리
  series   d(일봉 최근 2년) / w(주봉 전체) / m(월봉 전체) 수정종가.
           t = 날짜(2000-01-01 기준 일수, 첫 값 + 차이값), c = 종가, k = 앞쪽 이평 계산용 여유 구간 길이
           (이동평균은 브라우저에서 계산 → 파일 크기 절반)

    python -m src.dashboard.export
"""
from __future__ import annotations

import json
import math
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from ..prices.db import DB_PATH

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "site"
EPOCH = pd.Timestamp("2000-01-01")
DAILY_YEARS = 2
LINE_MA = {"d": 120, "w": 30, "m": 10}    # 차트 주황선 (일봉 120일선 / 30주선 / 10개월선)
BADGE_MA = {"d": 50, "w": 30, "m": 10}    # 일·주·월 뱃지 기준


# --------------------------------------------------------------------------- 유틸
def _sig(x: float, n: int = 5):
    """유효숫자 n자리로 줄여 파일 크기를 줄인다."""
    if x is None or not np.isfinite(x):
        return None
    if x == 0:
        return 0
    d = n - 1 - int(math.floor(math.log10(abs(x))))
    v = round(x, d) if d > 0 else round(x, d)
    return int(v) if d <= 0 else v


def _pct(x, nd=4):
    return None if x is None or pd.isna(x) else round(float(x), nd)


def _enc_dates(idx: pd.DatetimeIndex) -> list[int]:
    days = ((idx - EPOCH).days).to_numpy()
    if len(days) == 0:
        return []
    return [int(days[0])] + np.diff(days).astype(int).tolist()


def _period_last(s: pd.Series, rule: str) -> pd.Series:
    """주·월별 마지막 거래일 종가 (인덱스 = 그 거래일)."""
    p = s.index.to_period(rule)
    return s[~p.duplicated(keep="last")]


def _series(s: pd.Series) -> tuple[dict, dict]:
    """수정종가 일별 시리즈 → (차트용 시리즈, 오늘·전일 뱃지)."""
    s = s.dropna()
    s = s[s > 0]
    out = {}
    if s.empty:
        return out, {"badges": None, "changed": False}
    last = s.index[-1]

    # 일봉: 최근 2년 + 50일선 계산용 여유
    start = last - pd.DateOffset(years=DAILY_YEARS)
    i0 = int(np.searchsorted(s.index, start))
    k = min(i0, LINE_MA["d"] - 1)
    d = s.iloc[i0 - k:]
    out["d"] = {"t": _enc_dates(d.index), "c": [_sig(v) for v in d.values], "k": k}

    w, m = _period_last(s, "W-SUN"), _period_last(s, "M")
    for key, x in (("w", w), ("m", m)):
        out[key] = {"t": _enc_dates(x.index), "c": [_sig(v) for v in x.values], "k": 0}

    # 뱃지: 오늘 vs 전 거래일 (진행 중인 주·월봉은 그날 종가로 마감했다고 보고 계산)
    def state(upto: int) -> dict:
        ss = s.iloc[:upto + 1]
        res = {"d": int(ss.iloc[-1] > ss.iloc[-BADGE_MA["d"]:].mean()) if len(ss) >= BADGE_MA["d"] else None}
        for key, rule in (("w", "W-SUN"), ("m", "M")):
            x = _period_last(ss, rule)
            res[key] = int(x.iloc[-1] > x.iloc[-BADGE_MA[key]:].mean()) if len(x) >= BADGE_MA[key] else None
        return res

    badges = state(len(s) - 1)
    prev = state(len(s) - 2) if len(s) > 1 else badges
    changed = any(badges[k] is not None and prev[k] is not None and badges[k] != prev[k] for k in badges)
    return out, {"badges": badges, "changed": changed}


# --------------------------------------------------------------------------- 메인
def _load(con, ids: list[str]) -> dict[str, pd.Series]:
    con.register("_ids", pd.DataFrame({"symbol_id": ids}))
    px = con.execute("""
        SELECT p.symbol_id, p.date, p.close FROM v_prices_adj p JOIN _ids USING (symbol_id)
        WHERE p.close > 0 ORDER BY 1, 2""").df()
    con.unregister("_ids")
    px["date"] = pd.to_datetime(px["date"])
    return {k: g.set_index("date")["close"] for k, g in px.groupby("symbol_id", sort=False)}


def _latest(con, ids: list[str]) -> pd.DataFrame:
    con.register("_ids", pd.DataFrame({"symbol_id": ids}))
    df = con.execute("""
        SELECT v.symbol_id, v.date, v.close, v.ret_1m, v.ret_3m, v.dd_52w, v.mcap, v.value,
               v.rs, v.rs_1m, v.rs_3m, v.rs_6m, v.rs_12m
        FROM v_daily v JOIN _ids USING (symbol_id)
        QUALIFY row_number() OVER (PARTITION BY v.symbol_id ORDER BY v.date DESC) = 1""").df()
    con.unregister("_ids")
    return df.set_index("symbol_id")


def _card(sid: str, s: pd.Series | None, lt: pd.Series | None, bench3m: float | None, **extra) -> dict:
    ser, bd = _series(s) if s is not None else ({}, {"badges": None, "changed": False})
    chg = None
    if s is not None and len(s) > 1:
        chg = float(s.iloc[-1] / s.iloc[-2] - 1)
    c = {"id": sid, **extra, "date": None, "chg_1d": _pct(chg), **bd, "series": ser}
    if lt is not None:
        c.update(date=str(lt["date"])[:10], ret_1m=_pct(lt["ret_1m"]), ret_3m=_pct(lt["ret_3m"]),
                 dd_52w=_pct(lt["dd_52w"]),
                 rel_3m=_pct(lt["ret_3m"] - bench3m) if bench3m is not None and pd.notna(lt["ret_3m"]) else None)
    return c


def _write_js(path: Path, var: str, key: str | None, obj) -> int:
    txt = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    if key is None:
        body = f"window.{var}={txt};\n"
    else:
        body = f"window.{var}=window.{var}||{{}};window.{var}[{json.dumps(key)}]={txt};\n"
    path.write_text(body, encoding="utf-8")
    return len(body.encode())


def export(con=None, verbose: bool = True, kr: bool = True) -> dict:
    """kr=False(해외 갱신): overview.js만 다시 만들고 국내 전용 파일(kr_*, rs, rs_px, nh, setup)은 건드리지 않는다."""
    import duckdb
    t = time.time()
    own = con is None
    con = con or duckdb.connect(str(DB_PATH), read_only=True)
    con.execute("SET enable_progress_bar=false")
    (SITE / "data").mkdir(parents=True, exist_ok=True)

    meta = con.execute("SELECT * FROM group_meta").df().set_index("group_id")
    grp = con.execute("""SELECT g.*, s.ticker, s.name AS sym_name, s.category, s.subclass, s.unit, s.asset_class
                         FROM groups g JOIN symbols s ON s.id = g.symbol_id
                         ORDER BY g.group_id, g.sort_order""").df()
    members = con.execute("""SELECT code, name, market, sector_id, upjong FROM sector_map WHERE included""").df()

    ids = sorted(set(grp["symbol_id"]) | (set(members["code"]) if kr else set()))   # 해외 갱신은 개별 종목 시계열 불필요
    series = _load(con, ids)
    latest = _latest(con, ids)

    def bench_3m(b):
        return float(latest.loc[b, "ret_3m"]) if b and b in latest.index and pd.notna(latest.loc[b, "ret_3m"]) else None

    sec_count = members.groupby("sector_id").size().to_dict()
    overview = {"generated": pd.Timestamp.now(tz="Asia/Seoul").strftime("%Y-%m-%d %H:%M"), "ma": LINE_MA, "badge_ma": BADGE_MA, "groups": []}
    for gid in ["KR_SECTOR", "US_SECTOR_ETF", "MACRO"]:
        if gid not in meta.index:
            continue
        m = meta.loc[gid]
        b3 = bench_3m(m["benchmark"])
        cards = []
        for _, r in grp[grp["group_id"] == gid].iterrows():
            sid = r["symbol_id"]
            sec = sid.split(":", 1)[1] if sid.startswith("KRSEC:") else None
            link = None
            if m["link_template"] and r["ticker"]:
                link = m["link_template"].format(ticker=r["ticker"])
            elif gid == "MACRO" and r["ticker"]:
                link = f"https://finance.yahoo.com/quote/{r['ticker']}"
            cards.append(_card(sid, series.get(sid), latest.loc[sid] if sid in latest.index else None, b3,
                               name=r["display_name"].replace("-", "·"), ticker=r["ticker"],
                               pinned=bool(r["pinned"]), order=int(r["sort_order"]),
                               category=r["category"], subclass=r["subclass"],
                               sector=sec, count=sec_count.get(sec) if sec else None, link=link))
        dates = [c["date"] for c in cards if c["date"]]
        overview["groups"].append({
            "id": gid, "title": m["title"], "benchmark": m["benchmark"], "sort": m["sort"],
            "footer": json.loads(m["card_footer"] or "[]"), "pinned_footer": json.loads(m["pinned_footer"] or "[]"),
            "asof": max(dates) if dates else None, "cards": cards})
    size = {"overview.js": _write_js(SITE / "data" / "overview.js", "DASH_OVERVIEW", None, overview)}

    if kr:
        # 섹터 구성 종목
        b3 = bench_3m("KS11")
        for sec, g in members.groupby("sector_id"):
            cards = []
            for _, r in g.iterrows():
                code = r["code"]
                lt = latest.loc[code] if code in latest.index else None
                c = _card(code, series.get(code), lt, b3, name=r["name"], market=r["market"], upjong=r["upjong"],
                          link=f"https://m.stock.naver.com/domestic/stock/{code}/total")
                if lt is not None:
                    c.update(mcap=_sig(lt["mcap"] / 1e8, 4) if pd.notna(lt["mcap"]) else None,
                             value=_sig(lt["value"] / 1e8, 4) if pd.notna(lt["value"]) else None,
                             rs=None if pd.isna(lt["rs"]) else int(lt["rs"]))
                cards.append(c)
            upj = g.groupby("upjong").size().sort_values(ascending=False)
            obj = {"sector": sec, "upjong": [[k, int(v)] for k, v in upj.items()], "cards": cards}
            size[f"kr_{sec}.js"] = _write_js(SITE / "data" / f"kr_{sec}.js", "DASH_SECTOR", sec, obj)

        # RS 랭킹 탭 (섹터 RRG + 종목 RS 표)
        from .rs_export import build as build_rs_tab
        size["rs.js"] = _write_js(SITE / "data" / "rs.js", "DASH_RS", None, build_rs_tab(con))
        from .rs_export import build_px
        size["rs_px.js"] = _write_js(SITE / "data" / "rs_px.js", "DASH_RS_PX", None, build_px(con))   # 사용자 정의 섹터용
        from .nh_export import build as build_nh
        size["nh.js"] = _write_js(SITE / "data" / "nh.js", "DASH_NH", None, build_nh(con))   # 신고가 탭
        from .setup_export import build as build_setup
        size["setup.js"] = _write_js(SITE / "data" / "setup.js", "DASH_SETUP", None, build_setup(con))   # 차트 셋업 탭

    if own:
        con.close()
    if verbose:
        tot = sum(size.values())
        print(f"  대시보드 데이터: {len(size)}개 파일, {tot / 1e6:.1f}MB "
              f"(overview {size['overview.js'] / 1e6:.2f}MB) → {SITE} ({time.time() - t:.0f}s)")
    return size


if __name__ == "__main__":
    export()
