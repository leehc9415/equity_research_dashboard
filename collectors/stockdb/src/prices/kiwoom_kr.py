"""키움 REST(ka10081 주식일봉차트)로 한국 종목 일봉을 받아 prices_kiwoom 에 저장.

venue
  KRX  정규장(KRX) 일봉. 종목코드 그대로 (005930)
  AL   KRX+NXT 통합(SOR) 일봉. 종목코드 + "_AL". NXT 개장(2025-03-04) 이후만 받는다.

원주가(upd_stkpc_tp=0)로 받고 기준가 = 종가 - 전일대비, 키움 수정비율 upd_rt 를 저장한다.
수정주가는 v_prices_kiwoom_adj 뷰가 upd_rt 가 있는 날만 반영해 계산한다(최신일 = 1, 아래 DDL 주석 참고).
기존 marcap 구간(~2026-09-11)과 OHLC·거래량 일치(2026-11-15 확인).

인증: 환경변수 KIWOOM_APPKEY, KIWOOM_SECRETKEY (키움 REST API 포털에서 발급한 실전 앱키).

대상: 현재 상장 코스피(리츠·인프라펀드 포함)·코스닥 주식 + ETF. 코넥스·ETN·스팩 제외 (Kiwoom.listing)

    python -m src.prices.kiwoom_kr backfill                       # 2019-01-01~ KRX + AL 전 대상 (이미 받은 구간 앞쪽만 받음)
    python -m src.prices.kiwoom_kr backfill --venue krx --part 1/4
    python -m src.prices.kiwoom_kr backfill --codes 005930,000660 --force
    python -m src.prices.kiwoom_kr daily                          # 최근 7일 덮어쓰기 (매일은 run_prices 가 호출)

오늘 봉은 KRX 18:10(시간외 단일가 종료) 이후, AL 20:10(NXT 애프터마켓 종료) 이후에만 받는다.
수집이 끝나면 KRX 일봉을 prices_daily(source='kiwoom')로 옮긴다(sync_prices_daily) → 기존 지표·대시보드가 그대로 씀.
상장주식수·시총: 같은 날 기존 행 값 → 키움 종목목록(ka10099 listCount) 스냅샷 → 직전 값 순으로 채움.
"""
from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from .db import connect

BASE_URL = "https://api.kiwoom.com"
NXT_START = "2025-03-04"
MIN_INTERVAL = 0.2    # 호출 시작 간격 하한(초당 5건). 응답이 평균 0.27초라 대개 대기 없이 바로 다음 호출
KST = "Asia/Seoul"
# ka10099 marketCode: 거래소 주식·인프라투자금융·리츠·ETF·코스닥 (2026-11-18: 910+2+23+1190+1837, 스팩 70 제외)
TARGET_MARKETS = {"0", "2", "6", "8", "10"}

DDL = """
CREATE TABLE IF NOT EXISTS prices_kiwoom (
  symbol_id  TEXT,
  venue      TEXT,       -- KRX / AL(KRX+NXT 통합)
  date       DATE,
  open       DOUBLE,     -- 원주가
  high       DOUBLE,
  low        DOUBLE,
  close      DOUBLE,
  ref_price  DOUBLE,     -- 종가 - 전일대비 (권리락 반영 기준가)
  volume     DOUBLE,
  value      DOUBLE,     -- 거래대금(원). 키움은 백만원 단위 → ×1e6, 백만원 미만 절사
  fetched_at TIMESTAMP,
  PRIMARY KEY (symbol_id, venue, date)
);
-- 키움 수정비율(%): 권리 변동일에만 값(액면분할 -59.93 등). 배당락 등 수정 없는 이벤트는 0
ALTER TABLE prices_kiwoom ADD COLUMN IF NOT EXISTS upd_rt DOUBLE;
-- ka10099 종목목록 스냅샷 (수집일의 최근 거래일 기준). 상장주식수 이력이 여기서 쌓인다
CREATE TABLE IF NOT EXISTS kiwoom_listing (
  date DATE, code TEXT, name TEXT, market TEXT, shares DOUBLE, nxt BOOLEAN,
  PRIMARY KEY (date, code)
);
-- 수정계수: 2026-09-15부터 키움 '전일대비'의 기준이 KRX 전일 종가가 아니게 되어(권리 변동 없는 날도 어긋남)
-- 기준가 연쇄 대신 upd_rt 가 있는 날만 반영한다. 기준가/전일종가가 upd_rt 와 0.02%(반올림 오차) 안에서
-- 맞으면 그 정확한 비율을, 아니면 upd_rt(소수 둘째 자리) 를 쓴다. 207940 2026-10-02: 기준가 비율이
-- 0.07% 어긋남 → upd_rt -0.77 사용.
CREATE OR REPLACE VIEW v_prices_kiwoom_adj AS
WITH r AS (
  SELECT *, CASE
      WHEN coalesce(upd_rt, 0) = 0 OR NOT (lag(close) OVER w > 0) THEN 1
      WHEN abs(ref_price / lag(close) OVER w / (1 + upd_rt / 100) - 1) < 0.0002
        THEN ref_price / lag(close) OVER w
      ELSE 1 + upd_rt / 100 END AS r
  FROM prices_kiwoom
  WINDOW w AS (PARTITION BY symbol_id, venue ORDER BY date)
), f AS (
  SELECT *, coalesce(exp(sum(ln(r)) OVER (PARTITION BY symbol_id, venue ORDER BY date
                ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING)), 1.0) AS adj_factor
  FROM r
)
SELECT symbol_id, venue, date,
       open * adj_factor AS open, high * adj_factor AS high,
       low * adj_factor AS low, close * adj_factor AS close,
       close AS close_raw, adj_factor, volume, value
FROM f;
"""
COLS = ["symbol_id", "venue", "date", "open", "high", "low", "close",
        "ref_price", "volume", "value", "upd_rt", "fetched_at"]


class Kiwoom:
    def __init__(self):
        key, secret = os.environ.get("KIWOOM_APPKEY"), os.environ.get("KIWOOM_SECRETKEY")
        if not key or not secret:
            raise RuntimeError("환경변수 KIWOOM_APPKEY, KIWOOM_SECRETKEY 를 설정하세요.")
        r = requests.post(f"{BASE_URL}/oauth2/token", timeout=30, json={
            "grant_type": "client_credentials", "appkey": key, "secretkey": secret})
        r.raise_for_status()
        j = r.json()
        if j.get("return_code") not in (None, 0) or not j.get("token"):
            raise RuntimeError(f"토큰 발급 실패: {j.get('return_msg')}")
        self.token = j["token"]
        self.s = requests.Session()
        self._last = 0.0

    def call(self, api_id: str, path: str, body: dict, next_key: str | None = None):
        """POST 한 번 → (응답 JSON, next_key 또는 None). 호출 제한이면 지수 대기 후 재시도."""
        headers = {"api-id": api_id, "authorization": f"Bearer {self.token}",
                   "Content-Type": "application/json;charset=UTF-8"}
        if next_key:
            headers.update({"cont-yn": "Y", "next-key": next_key})
        for attempt in range(6):
            time.sleep(max(0.0, self._last + MIN_INTERVAL - time.monotonic()))
            self._last = time.monotonic()
            r = self.s.post(f"{BASE_URL}{path}", headers=headers, json=body, timeout=30)
            j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code == 429 or "초과" in str(j.get("return_msg", "")):
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            if j.get("return_code") not in (None, 0):
                raise RuntimeError(f"{body}: {j.get('return_msg')}")
            return j, (r.headers.get("next-key") if r.headers.get("cont-yn") == "Y" else None)
        raise RuntimeError(f"{body}: 호출 제한 재시도 초과")

    def daily_page(self, code: str, base_dt: str, next_key: str | None):
        """일봉 한 페이지(최대 600일) → (rows, next_key 또는 None)."""
        j, nk = self.call("ka10081", "/api/dostk/chart",
                          {"stk_cd": code, "base_dt": base_dt, "upd_stkpc_tp": "0"}, next_key)
        return j.get("stk_dt_pole_chart_qry") or [], nk

    def listing(self) -> pd.DataFrame:
        """수집 대상(현재 상장) → code, name, market, shares, nxt. ka10099 기준.
        mrkt_tp=0 은 거래소 전 상품(주식·ETF·ETN·리츠 …)을, 10 은 코스닥을 준다. 그중 TARGET_MARKETS 만,
        이름에 '스팩'이 든 종목은 뺀다. 코넥스·ETN·뮤추얼펀드는 marketCode 로 빠진다.
        NXT 가능(nxtEnable=Y)이 아닌 종목은 _AL 이 KRX 와 같은 값을 준다(000020 확인)."""
        rows = []
        for m in ("0", "10"):
            nk = None
            while True:
                j, nk = self.call("ka10099", "/api/dostk/stkinfo", {"mrkt_tp": m}, nk)
                rows += [{"code": x["code"], "name": x.get("name"), "market": x.get("marketName"),
                          "shares": pd.to_numeric(x.get("listCount"), errors="coerce"),
                          "nxt": x.get("nxtEnable") == "Y"}
                         for x in j.get("list", [])
                         if x.get("marketCode") in TARGET_MARKETS and "스팩" not in x.get("name", "")]
                if not nk:
                    break
        return pd.DataFrame(rows).drop_duplicates("code")


def parse_rows(rows: list[dict], symbol_id: str, venue: str, start: str) -> pd.DataFrame:
    """ka10081 응답 행 → prices_kiwoom 행. start 이전과 날짜 없는 행은 버린다."""
    if not rows:
        return pd.DataFrame(columns=COLS)
    d = pd.DataFrame(rows)
    num = lambda c: pd.to_numeric(d[c], errors="coerce").abs()      # 가격 앞 +/- 는 등락 표시
    close = num("cur_prc")
    dts = pd.to_datetime(d["dt"].astype(str), format="%Y%m%d", errors="coerce")
    d, close, dts = d[dts >= pd.Timestamp(start)], close[dts >= pd.Timestamp(start)], dts[dts >= pd.Timestamp(start)]
    out = pd.DataFrame({
        "symbol_id": symbol_id, "venue": venue,
        "date": dts.dt.date,
        "open": num("open_pric"), "high": num("high_pric"), "low": num("low_pric"), "close": close,
        "ref_price": close - pd.to_numeric(d["pred_pre"], errors="coerce").fillna(0),
        "volume": num("trde_qty"), "value": num("trde_prica") * 1e6,
        "upd_rt": pd.to_numeric(d["upd_rt"], errors="coerce") if "upd_rt" in d else None,
        "fetched_at": pd.Timestamp(datetime.now()),
    })
    for c in ("open", "high", "low"):                  # 거래 없는 날 0 → 종가
        out[c] = out[c].where(out[c] > 0, out["close"])
    out = out.dropna(subset=["date", "close"])
    return out[out["close"] > 0][COLS]


def fetch(api: Kiwoom, code: str, venue: str, start: str, base_dt: str) -> pd.DataFrame:
    stk = code if venue == "KRX" else f"{code}_AL"
    frames, nk = [], None
    while True:
        rows, nk = api.daily_page(stk, base_dt, nk)
        frames.append(parse_rows(rows, code, venue, start))
        oldest = min((x.get("dt", "") for x in rows), default="")
        if not nk or not rows or oldest < start.replace("-", ""):
            break
    return pd.concat(frames, ignore_index=True).drop_duplicates(["date"])


def upsert(con, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    con.register("_k", df[COLS])
    con.execute(f"INSERT OR REPLACE INTO prices_kiwoom ({', '.join(COLS)}) SELECT {', '.join(COLS)} FROM _k")
    con.unregister("_k")
    return len(df)


def today_cutoff(venue: str) -> str:
    """오늘 봉 확정 시각(KST) 이후면 오늘, 아니면 어제를 base_dt 로.
    KRX 18:10: 거래량·거래대금이 시간외 단일가(~18:00)까지 포함돼야 KRX 일봉과 같다. AL 20:10: NXT 애프터마켓 종료."""
    now = datetime.now(ZoneInfo(KST))
    hm = (18, 10) if venue == "KRX" else (20, 10)
    d = now if (now.hour, now.minute) >= hm else now - timedelta(days=1)
    return d.strftime("%Y%m%d")


def run(con, codes: list[str] | None, venues: list[str], start: str, skip_done: bool, job: str,
        part: tuple[int, int] = (1, 1)):
    """codes=None 이면 listing() 의 현재 상장 종목 전체. part=(i, n): 그중 i번째 묶음만."""
    api = Kiwoom()
    # shortcut: 현재 상장·현재 NXT 종목 기준 → 상장폐지 종목, 과거에만 NXT 에서 거래된 종목은 빠짐. 필요하면 --codes 로
    lst = api.listing()
    nxt = set(lst.loc[lst["nxt"], "code"])
    codes = codes or sorted(lst["code"])
    i, n = part
    codes = codes[(i - 1) * len(codes) // n: i * len(codes) // n]
    print(f"  대상 {len(codes)}종목 (현재 상장 {len(lst)}, NXT 가능 {len(nxt)} → AL 은 이 종목만)")
    base = {v: today_cutoff(v) for v in venues}   # 시작 때 한 번: 실행 중 18:10 을 넘겨도 종목마다 기준일이 같게
    first = {}
    if skip_done:   # 이어받기: 그 venue 의 시작일 근처(10일 이내)부터 이미 있는 종목은 건너뜀
        first = {(r[0], r[1]): r[2] for r in con.execute(
            "SELECT symbol_id, venue, min(date) FROM prices_kiwoom GROUP BY 1, 2").fetchall()}
    t, n_rows, failed, buf = time.time(), 0, [], []
    for i, code in enumerate(codes, 1):
        for v in venues:
            if v == "AL" and code not in nxt:
                continue
            s = max(start, NXT_START) if v == "AL" else start
            have = first.get((code, v))
            bd = base[v]
            if have is not None:
                if have <= datetime.strptime(s, "%Y-%m-%d").date() + timedelta(days=10):
                    continue
                bd = (have - timedelta(days=1)).strftime("%Y%m%d")   # 이미 받은 구간 앞쪽만 (원주가라 기준일 무관)
            try:
                buf.append(fetch(api, code, v, s, bd))
            except Exception as e:  # noqa: BLE001 - 상장폐지·NXT 미거래 등은 기록만
                failed.append(f"{code}{'' if v == 'KRX' else '_AL'}: {e}"[:120])
        if i % 50 == 0 or i == len(codes):
            n_rows += upsert(con, pd.concat(buf, ignore_index=True)) if buf else 0
            buf = []
            print(f"  {i}/{len(codes)} 종목, {n_rows:,}행, 실패 {len(failed)}, {time.time() - t:.0f}초", flush=True)
    for f in failed[:20]:
        print("  실패", f)
    con.execute("INSERT INTO collect_log VALUES (?, ?, ?, ?, ?, NULL, NULL, ?)",
                [datetime.now(), job, "ok" if not failed else "partial", len(codes), n_rows,
                 ";".join(failed)[:500]])
    # 종목목록 스냅샷 = 지금 받은 KRX 최근 거래일의 상장주식수
    con.register("_l", lst)
    con.execute("""INSERT OR REPLACE INTO kiwoom_listing
        SELECT (SELECT max(date) FROM prices_kiwoom WHERE venue = 'KRX'), code, name, market, shares, nxt FROM _l""")
    con.unregister("_l")


# 키움 KRX 일봉 → prices_daily 행. 기준가 = 전일 종가 × 권리 변동 비율 (kr.ADJ_SQL 로 다시 계산해도 같은 수정계수)
# 거래대금: marcap 정확값이 있으면 유지(키움은 백만원 절사). 상장주식수: 기존 행 → 종목목록 스냅샷 → 직전 값
SYNC_SQL = """
CREATE OR REPLACE TEMP TABLE _sync AS
WITH k AS (
  SELECT p.symbol_id, p.date, p.open, p.high, p.low, p.close, p.volume, p.value AS kw_value, a.adj_factor,
         lag(p.close) OVER w * lag(a.adj_factor) OVER w / a.adj_factor AS ref_price
  FROM prices_kiwoom p JOIN v_prices_kiwoom_adj a USING (symbol_id, venue, date)
  WHERE p.venue = 'KRX'
  WINDOW w AS (PARTITION BY p.symbol_id ORDER BY p.date)
), j AS (
  SELECT k.*, CASE WHEN o.source IN ('marcap', 'kiwoom') THEN o.value END AS old_value,
         coalesce(o.shares, l.shares) AS shares0
  FROM k
  LEFT JOIN prices_daily o ON o.symbol_id = k.symbol_id AND o.date = k.date
       AND o.source IN ('marcap', 'naver', 'naver_fix', 'kiwoom')
  LEFT JOIN kiwoom_listing l ON l.code = k.symbol_id AND l.date = k.date
)
SELECT symbol_id, date, open, high, low, close, ref_price, adj_factor, volume,
       coalesce(old_value, kw_value) AS value, FALSE AS value_is_est,
       last_value(shares0 IGNORE NULLS) OVER (PARTITION BY symbol_id ORDER BY date) AS shares
FROM j;
"""


def sync_prices_daily(con) -> int:
    """prices_kiwoom(KRX) 전체를 prices_daily(source='kiwoom')로 옮긴다.
    키움이 가진 종목·기간의 기존 행(marcap·naver 등)은 지우고 바꾼다. 그 밖(상장폐지 종목 등)은 그대로."""
    con.execute(SYNC_SQL)
    con.execute("BEGIN")
    try:
        con.execute("""DELETE FROM prices_daily p USING
            (SELECT symbol_id, min(date) lo, max(date) hi FROM _sync GROUP BY 1) r
            WHERE p.symbol_id = r.symbol_id AND p.date BETWEEN r.lo AND r.hi""")
        con.execute("""INSERT INTO prices_daily (symbol_id, date, open, high, low, close, ref_price, adj_factor,
                volume, value, value_is_est, mcap, shares, source, fetched_at)
            SELECT symbol_id, date, open, high, low, close, ref_price, adj_factor, volume, value, value_is_est,
                   close * shares, shares, 'kiwoom', now() FROM _sync""")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    n = con.execute("SELECT count(*) FROM _sync").fetchone()[0]
    con.execute("DROP TABLE _sync")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["backfill", "daily"])
    ap.add_argument("--venue", choices=["krx", "al", "both"], default="both")
    ap.add_argument("--start", default="2019-01-01", help="backfill 시작일")
    ap.add_argument("--days", type=int, default=7, help="daily: 최근 N일(달력) 덮어쓰기")
    ap.add_argument("--part", default="1/1", help="i/n 으로 나눠 실행")
    ap.add_argument("--codes", default=None, help="쉼표 구분 종목코드")
    ap.add_argument("--force", action="store_true", help="backfill: 이미 받은 종목도 다시")
    a = ap.parse_args()

    con = connect()
    con.execute(DDL)
    # zfill: PowerShell 이 005930,000660 을 숫자 배열로 넘겨 앞자리 0이 사라지는 경우 복원
    codes = [c.strip().zfill(6) for c in a.codes.split(",")] if a.codes else None
    part = tuple(map(int, a.part.split("/")))
    venues = {"krx": ["KRX"], "al": ["AL"], "both": ["KRX", "AL"]}[a.venue]
    if a.mode == "backfill":
        run(con, codes, venues, a.start, skip_done=not a.force, job="kiwoom_backfill", part=part)
    else:
        start = str((pd.Timestamp.today() - pd.Timedelta(days=a.days)).date())
        run(con, codes, venues, start, skip_done=False, job="kiwoom_daily", part=part)
    print(f"  prices_daily 동기화: {sync_prices_daily(con):,}행 (지표는 python -m src.indicators.build)")
    con.execute("CHECKPOINT")
    con.close()


if __name__ == "__main__":
    main()
