"""차트 셋업 탭 데이터 → site/data/setup.js

장마감 일봉(수정주가)으로 7가지 셋업을 최근 20거래일 동안 매일 판정하고, 마지막 날 걸린 종목을 보낸다.
같은 셋업에 며칠째 연속으로 걸려 있는지(신규 / N일째)도 함께 계산한다.
대상: 코스피·코스닥 보통주(스팩·우선주 제외). 시총·거래대금 하한은 화면에서.

셋업 정의
  gap    거래량 동반 갭상승   시가 > 전일 고가 & 종가 ≥ 전일 종가 +3% & 거래량 ≥ 직전 50일 평균 × 2
  lead   RS 선두              MTT 충족 & RS ≥ 85 & RS 1M ≥ 85 & 종가 ≥ 52주 고가 × 0.82
                              (스탁이지 '강세 선두' 역산: 2일 18종목 전부 일치)
  trend  추세 시작            MTT 가격 조건(RS 조건 없음) & 종가 ≥ 52주 고가 × 0.8 & 종가 ≤ 52주 저가 × 2 & RS 1M ≥ 50
                              (스탁이지 '추세 시작' 역산: 22종목 중 20개 일치), 52주 저점 대비 덜 오른 순
  vcp    VCP                  MTT 충족 & 최근 120일 고점 이후 '의미 있는' 수축(지그재그 임계 = 50일 평균 ATR%×2, 최소 4%) 2회 이상
                              & 첫 수축이 가장 깊고 10~40% & 마지막 2~4회 수축이 점점 얕아짐 & 마지막 수축 ≤ max(10%, ATR%×3)
                              & 10일 평균 거래량 < 50일 평균 × 0.8
                              & 종가가 피봇(마지막 수축의 고점) −6% ~ 0% (돌파 직전)
  cup    컵위드핸들           RS ≥ 70 & 종가 > 50·200일선 (추세 필터)
                              피봇 = 4% 지그재그로 확정된 마지막 스윙 고점(3~40일 전) — 오른쪽 고점보다 3% 이상 낮은
                                     핸들 안의 낮아진 고점(포켓 피봇, 52주 최고가보다 낮은 지점)
                              핸들 = 피봇 이후, 깊이 ≤ 10%, 이후 고가 ≤ 피봇×1.05
                              컵 = 피봇 이전 스윙 고점(7~65주 전, 직전 20일 최고)을 왼쪽 고점으로, 바닥 조정 12~35%
                              회복률(현재가/왼쪽 고점) 95~115% · 핸들 저점이 컵 위쪽 절반 · 현재가가 피봇 −3% ~ +5%
  ipo    IPO 셋업             상장 후 250거래일 이내 & 종가가 상장 첫날 시가의 ±10% 이내 (상장 직후 시가로 되돌아옴)
  weekv  주봉 거래량 급증 상승 최근 5거래일 거래량 ≥ 직전 50일의 5일 평균 × 2 & 최근 5거래일 수익률 ≥ +5%

MTT(미너비니 트렌드 템플릿) = 종가>150·200일선, 150일선>200일선, 200일선 1개월 상승, 50일선>150·200일선,
                             종가>50일선, 종가 ≥ 52주 저가×1.3, 종가 ≥ 52주 고가×0.75, RS ≥ 70
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DAYS = 20          # 판정하는 최근 거래일 수 (연속 일수 계산용)
HIST = 420         # 지표 계산에 쓰는 과거 거래일 수
SPARK = 60

SETUPS = [
    ("gap", "거래량 동반 갭상승", "전일 고가 위로 갭을 띄우고 3% 이상 오르며 거래량이 50일 평균의 2배 이상"),
    ("lead", "RS 선두", "추세 템플릿을 충족하며 RS·1개월 RS 모두 85 이상, 52주 고점 근처의 시장 주도주"),
    ("trend", "추세 시작", "이동평균이 정배열로 막 바뀐 상승 2단계 초입, 52주 저점 대비 아직 덜 오른 종목"),
    ("vcp", "VCP", "고점 이후 조정 폭이 점점 줄며 거래량이 마르는 변동성 수축, 피봇 돌파 직전"),
    ("cup", "컵위드핸들", "U자로 왼쪽 고점을 회복한 뒤 핸들에서 낮아진 고점(포켓 피봇) 돌파를 노리는 자리"),
    ("ipo", "IPO 셋업", "상장 1년 이내, 상장 첫날 시가 부근으로 되돌아온 종목"),
    ("weekv", "주봉 거래량 급증 상승", "최근 1주(5거래일) 거래량이 평소 2배 이상 터지며 5% 이상 상승"),
]


def _ma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        c = np.cumsum(np.insert(x, 0, 0.0))
        out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def _roll(x: np.ndarray, n: int, fn) -> np.ndarray:
    s = pd.Series(x)
    return getattr(s.rolling(n, min_periods=1), fn)().to_numpy()


def _zigzag(hi: np.ndarray, lo: np.ndarray, th: float = 0.04):
    """첫 봉(고점)에서 시작하는 지그재그: [(종류, 인덱스, 가격)], 마지막 진행 중 구간 포함."""
    piv = [("H", 0, hi[0])]
    trend, ext_i, ext = -1, 0, lo[0]
    for i in range(1, len(hi)):
        if trend < 0:
            if lo[i] < ext:
                ext, ext_i = lo[i], i
            if hi[i] >= ext * (1 + th):
                piv.append(("L", ext_i, ext)); trend, ext, ext_i = 1, hi[i], i
        else:
            if hi[i] > ext:
                ext, ext_i = hi[i], i
            if lo[i] <= ext * (1 - th):
                piv.append(("H", ext_i, ext)); trend, ext, ext_i = -1, lo[i], i
    piv.append(("L" if trend < 0 else "H", ext_i, ext))
    return piv


def _atrp(hi, lo, c, t, n=50):
    """최근 n일 평균 ATR% (True Range / 종가)."""
    i0 = max(1, t - n + 1)
    pc = c[i0 - 1:t]
    tr = np.maximum(hi[i0:t + 1] - lo[i0:t + 1], np.maximum(abs(hi[i0:t + 1] - pc), abs(lo[i0:t + 1] - pc)))
    return float(np.mean(tr / c[i0:t + 1]))


def _vcp(hi, lo, c, t):
    """VCP — 수축은 '의미 있는' 것만 센다: 지그재그 임계값 = 최근 50일 평균 ATR% × 2 (최소 4%).

    베이스 시작 = 최근 120일 최고가(15거래일 이상 경과). 그 뒤 고점→저점 수축을 차례로 잰다.
    수축 2회 이상 · 첫 수축(가장 깊어야 함) 10~40% · 마지막 2~4회 수축이 매번 직전보다 얕음
    · 마지막 수축 ≤ max(10%, ATR%×3)
    · 종가가 피봇(마지막 수축의 시작 고점) −6% ~ 0%.
    """
    w0 = max(0, t - 119)
    i0 = w0 + int(np.argmax(hi[w0:t + 1]))           # 베이스 시작 = 120일 최고가
    if t - i0 < 15:
        return None
    th = max(0.04, 2 * _atrp(hi, lo, c, t))
    piv = _zigzag(hi[i0:t + 1], lo[i0:t + 1], th)
    depths, last_h = [], None
    for a, b in zip(piv, piv[1:]):
        if a[0] == "H" and b[0] == "L":
            depths.append(1 - b[2] / a[2]); last_h = a[2]
    last_max = max(0.10, 1.5 * th)                    # 변동성 큰 종목은 마지막 수축 한도도 넓힘 (ATR%×3)
    if len(depths) < 2 or not (0.10 <= depths[0] <= 0.40) or depths[-1] > last_max:
        return None
    if max(depths[1:]) >= depths[0]:
        return None
    tail = depths[-4:]
    if any(d2 >= d1 for d1, d2 in zip(tail, tail[1:])):
        return None
    pivot = last_h
    gap = c[t] / pivot - 1
    if not (-0.06 <= gap <= 0.0):
        return None
    return {"n": len(depths), "depths": [round(d * 100, 1) for d in tail], "pivot": pivot, "pgap": gap * 100,
            "th": th * 100}


def _swings(hi, lo, th=0.04):
    """지그재그 스윙: [(종류, 인덱스, 가격)]. 마지막 진행 중 극점은 확정되지 않았으므로 제외."""
    piv, trend = [], 0
    hi_i, lo_i = 0, 0
    for i in range(1, len(hi)):
        if hi[i] > hi[hi_i]:
            hi_i = i
        if lo[i] < lo[lo_i]:
            lo_i = i
        if trend >= 0 and lo[i] <= hi[hi_i] * (1 - th) and hi_i < i:
            if trend == 0 or not piv or piv[-1][0] != "H":
                piv.append(("H", hi_i, hi[hi_i]))
            trend, lo_i = -1, i
        elif trend <= 0 and hi[i] >= lo[lo_i] * (1 + th) and lo_i < i:
            if trend == 0 or not piv or piv[-1][0] != "L":
                piv.append(("L", lo_i, lo[lo_i]))
            trend, hi_i = 1, i
    return piv


def _cup(hi, lo, c, t, pocket=1.03):
    """컵위드핸들 — 피봇을 52주 최고가가 아닌 '핸들 안의 낮아진 고점'(포켓 피봇)으로 잡는다.

    1) 피봇: 4% 지그재그로 확정된 마지막 스윙 고점(3~40거래일 전).
       이후 고가가 피봇의 105%를 넘으면 이미 이탈로 보고 제외.
       핸들 깊이 = 1 − (피봇 이후 최저가 / 피봇) ≤ 10%.
    2) 컵: 피봇 이전의 스윙 고점을 최근 것부터 거슬러 가며 '왼쪽 고점' 후보로 본다(직전 20일 중 최고가여야 함).
       바닥 = 왼쪽 고점 ~ 피봇 사이 최저가. 조정폭 12~35%, 왼쪽 고점 → 피봇 7~65주,
       왼쪽 고점→바닥, 바닥→피봇 각 10거래일 이상.
    3) 오른쪽 고점(바닥 이후 최고가)이 피봇보다 3% 이상 높아야 함 → 피봇은 핸들 안에서 낮아진 고점.
    4) 회복률 = 현재가 / 왼쪽 고점, 95~115%. 핸들 저점이 컵 위쪽 절반. 현재가가 피봇 −3% ~ +5%.
    """
    w0 = max(0, t - 380)
    sw = [(k, i + w0, p) for k, i, p in _swings(hi[w0:t + 1], lo[w0:t + 1])]
    highs = [x for x in sw if x[0] == "H" and t - 40 <= x[1] <= t - 3]
    if not highs:
        return None
    _, ip, pivot = highs[-1]
    gap = c[t] / pivot - 1
    if not (-0.03 <= gap <= 0.05) or hi[ip + 1:t + 1].max() > pivot * 1.05:
        return None
    hl = lo[ip + 1:t + 1].min()
    hdepth = 1 - hl / pivot
    if hdepth > 0.10:
        return None
    for _, il, lip in reversed([x for x in sw if x[0] == "H" and x[1] < ip]):
        if ip - il < 35:
            continue
        if ip - il > 325:
            break
        if lip < hi[max(0, il - 20):il].max(initial=0):
            continue
        ib = il + int(np.argmin(lo[il:ip + 1]))
        bottom = lo[ib]
        depth = 1 - bottom / lip
        if not (0.12 <= depth <= 0.35 and ib - il >= 10 and ip - ib >= 10):
            continue
        rec = c[t] / lip
        if not (0.95 <= rec <= 1.15) or pivot < lip * 0.95 or hl < bottom + 0.5 * (lip - bottom):
            continue
        right = hi[ib:ip + 1].max()
        if right < pivot * pocket:
            return None
        return {"depth": depth * 100, "rec": rec * 100, "pivot": pivot, "pgap": gap * 100,
                "hdepth": hdepth * 100, "rpk": (right / pivot - 1) * 100, "weeks": (ip - il) / 5, "hdays": t - ip}
    return None


def _r(v, nd=1):
    return None if v is None or not np.isfinite(v) else round(float(v), nd)


def build(con) -> dict:
    cal = con.execute(f"SELECT date FROM prices_daily WHERE symbol_id='KS11' ORDER BY date DESC LIMIT {HIST}").df()["date"]
    d0 = cal.min()
    px = con.execute("""
        SELECT p.symbol_id code, p.date, p.open, p.high, p.low, p.close,
               CASE WHEN p.volume > 0 THEN p.volume / p.adj_factor ELSE 0 END AS vol, p.value, p.mcap
        FROM v_prices_adj p JOIN v_rs_universe u USING (symbol_id)
        WHERE p.date >= ? AND p.close > 0 AND p.source IN ('marcap', 'naver', 'naver_fix')
        ORDER BY 1, 2""", [d0]).df()
    px["date"] = pd.to_datetime(px["date"]).dt.date
    first = con.execute("""SELECT p.symbol_id code, min(p.date) d FROM prices_daily p JOIN v_rs_universe u USING (symbol_id)
        WHERE p.source IN ('marcap', 'naver', 'naver_fix') GROUP BY 1""").df()
    first = first.assign(d=pd.to_datetime(first["d"]).dt.date).set_index("code")["d"]
    db_start = first.min()
    days = sorted(pd.to_datetime(cal).dt.date)[-DAYS:]
    rs = con.execute("SELECT symbol_id code, date, rs, rs_1m FROM rs_daily WHERE date >= ?", [days[0]]).df()
    rs["date"] = pd.to_datetime(rs["date"]).dt.date
    rs = {(r.code, r.date): (r.rs, r.rs_1m) for r in rs.itertuples(index=False)}
    last = days[-1]

    hits = {k: [] for k, _, _ in SETUPS}
    spark = {}
    for code, g in px.groupby("code", sort=False):
        d = g["date"].to_numpy()
        o, h, l, c = (g[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
        v, val, mc = g["vol"].to_numpy(dtype=float), g["value"].to_numpy(dtype=float), g["mcap"].to_numpy(dtype=float)
        n = len(c)
        if n < 6 or d[-1] != last:
            continue
        ma50, ma150, ma200 = _ma(c, 50), _ma(c, 150), _ma(c, 200)
        h52, l52 = _roll(h, 250, "max"), _roll(l, 250, "min")
        # 상장일이 DB 시작(2021-01) 이후이고 계산 창 안에 있어야 상장 후 경과일을 셀 수 있음
        listed_new = first.get(code) is not None and first[code] > db_start and first[code] == d[0]
        idx = {dd: i for i, dd in enumerate(d)}
        flags = {k: [] for k in hits}
        info = {}
        for day in days:
            t = idx.get(day)
            if t is None or t < 1 or v[t] <= 0:
                for k in flags:
                    flags[k].append(False)
                continue
            r_rs, r_1m = rs.get((code, day), (np.nan, np.nan))
            mtt_px = (t >= 220 and c[t] > ma150[t] and c[t] > ma200[t] and ma150[t] > ma200[t]
                      and ma200[t] > ma200[t - 21] and ma50[t] > ma150[t] and ma50[t] > ma200[t] and c[t] > ma50[t]
                      and c[t] >= 1.3 * l52[t] and c[t] >= 0.75 * h52[t])
            mtt = mtt_px and r_rs >= 70
            v50 = v[max(0, t - 50):t].mean() if t >= 20 else np.nan
            res = {}
            res["gap"] = (o[t] > h[t - 1] and c[t] >= c[t - 1] * 1.03 and v50 > 0 and v[t] >= 2 * v50) and \
                {"gap": (o[t] / h[t - 1] - 1) * 100, "vx": v[t] / v50}
            res["lead"] = (mtt and r_rs >= 85 and r_1m >= 85 and c[t] >= 0.82 * h52[t]) and {"gap52": (c[t] / h52[t] - 1) * 100}
            res["trend"] = (mtt_px and c[t] >= 0.8 * h52[t] and c[t] <= 2 * l52[t] and r_1m >= 50) and \
                {"gap52": (c[t] / h52[t] - 1) * 100, "upl": (c[t] / l52[t] - 1) * 100}
            vcp = _vcp(h, l, c, t) if mtt and t >= 60 and v[max(0, t - 9):t + 1].mean() < 0.8 * v50 else None
            res["vcp"] = vcp or False
            cup = _cup(h, l, c, t) if t >= 200 and r_rs >= 70 and c[t] > ma50[t] and c[t] > ma200[t] else None
            res["cup"] = cup or False
            res["ipo"] = (listed_new and t <= 250 and abs(c[t] / o[0] - 1) <= 0.10) and \
                {"days": t + 1, "vsopen": (c[t] / o[0] - 1) * 100, "open0": o[0]}
            if t >= 55:
                v5, base5 = v[t - 4:t + 1].sum(), v[t - 54:t - 4].sum() / 10
                r5 = c[t] / c[t - 5] - 1
                res["weekv"] = (base5 > 0 and v5 >= 2 * base5 and r5 >= 0.05) and {"vx": v5 / base5, "r5": r5 * 100}
            else:
                res["weekv"] = False
            for k in flags:
                flags[k].append(bool(res[k]))
            if day == last:
                info = {k: res[k] for k in res if res[k]}
                base = dict(close=c[t], chg=(c[t] / c[t - 1] - 1) * 100, value=val[t] / 1e8,
                            vratio=val[t] / val[max(0, t - 20):t].mean() if t >= 5 and val[max(0, t - 20):t].mean() > 0 else np.nan,
                            v20=val[max(0, t - 20):t].mean() / 1e8 if t >= 1 else np.nan,
                            mcap=mc[t] / 1e8 if np.isfinite(mc[t]) else np.nan, rs=r_rs, rs1m=r_1m, mtt=bool(mtt))
        if not info:
            continue
        for k, m in info.items():
            fl = flags[k]
            streak = 0
            for x in fl[::-1]:
                if x:
                    streak += 1
                else:
                    break
            hits[k].append({"code": code, "streak": streak, **{kk: _r(vv, 2) if isinstance(vv, float) else vv for kk, vv in base.items()},
                            "m": {kk: (_r(vv, 2) if isinstance(vv, (float, np.floating)) else vv) for kk, vv in m.items()}})
        lo_i = max(0, n - SPARK)
        spark[code] = [float(f"{x:.5g}") for x in c[lo_i:]]

    codes = sorted(spark)
    meta = con.execute("""SELECT s.id, s.name, s.subclass, m.se_l, m.se_m FROM symbols s
        LEFT JOIN se_sector_map m ON m.code = s.id WHERE s.id IN (SELECT unnest(?))""", [codes]).df()
    stocks = {r.id: [r["name"], r.subclass, r.se_l if pd.notna(r.se_l) else None, r.se_m if pd.notna(r.se_m) else None]
              for _, r in meta.iterrows()}
    return {"asof": str(last), "days": [str(x) for x in days],
            "setups": [{"id": k, "name": nm, "desc": ds, "n": len(hits[k])} for k, nm, ds in SETUPS],
            "hits": hits, "stocks": stocks, "spark": spark}
