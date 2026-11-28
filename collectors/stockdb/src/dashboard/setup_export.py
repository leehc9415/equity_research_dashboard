"""차트 셋업 탭 데이터 → site/data/setup.js

장마감 일봉(수정주가)으로 7가지 셋업을 최근 20거래일 동안 매일 판정하고, 마지막 날 걸린 종목을 보낸다.
같은 셋업에 며칠째 연속으로 걸려 있는지(신규 / N일째)도 함께 계산한다.
대상: 코스피·코스닥 보통주(스팩·우선주 제외). 시총·거래대금 하한은 화면에서.

셋업 정의
  gap    거래량 동반 갭상승   시가 > 전일 고가 & 종가 ≥ 전일 종가 +3% & 거래량 ≥ 직전 50일 평균 × 2
  lead   RS 선두              MTT 충족 & RS ≥ 75 & RS 1M ≥ 75 & 종가 ≥ 52주 고가 × 0.80 & 시총 ≥ 2,000억 & 20일 평균 거래대금 ≥ 30억
                              (스탁이지 '강세 선두' 누적 15일 157건 역산: 재현율 93%·정밀도 84%)
  trend  추세 시작            MTT 가격 조건(RS 조건 없음) & 종가 ≥ 52주 고가 × 0.8 & 종가 ≤ 52주 저가 × 2 (RS 조건 없음)
                              & 시총 ≥ 300억 (스탁이지 '추세 시작' 역산), 52주 저점 대비 덜 오른 순
  vcp    VCP                  MTT 충족 & 최근 120일 고점 이후 '의미 있는' 수축(지그재그 임계 = 50일 평균 ATR%×2, 최소 4%) 2회 이상
                              & 첫 수축이 가장 깊고 10~40% & 마지막 2~4회 수축이 점점 얕아짐 & 마지막 수축 ≤ max(10%, ATR%×3)
                              & 10일 평균 거래량 < 50일 평균 × 0.8
                              & 종가가 피봇(마지막 수축의 고점) −6% ~ 0% (돌파 직전)
  cup    컵위드핸들 v2        (스탁이지 '컵위드핸들' 64건 역산, 자세한 정의는 _cup_eval)
                              추세: 200일선 상승 · 50>150>200일선 · 종가 > 150·200일선 · 종가 ≥ 50일선×0.97 · 52주 고가×0.8
                              피봇 = 5~25일 전 고점(이후 +8%까지 돌파 허용), 핸들 깊이 ≤ 20%, 종가가 피봇 −10% ~ +5%
                              컵 조정폭 10~25%, 회복률(종가/왼쪽 고점) 85~110%, 핸들 저점이 컵 위쪽 절반
                              RS ≥ 65 · 시총 ≥ 200억
  htf    HTF 급등 깃발        최근 42거래일 고점이 그 전 저점 대비 +50% 이상 · 고점 이후 하락 ≤25% · 종가>50일선 (고점 경과일 제한 없음, 42일 창 안)
                              · 최근 10일 평균 거래량 / 그 전 50일 평균 ≤1.10 · 최근 10일 평균 고저폭(고가−저가)/종가 / 그 전 60일 평균 ≤1.26
                              · RS ≥ 80 · 시총 ≥ 1,500억 (스탁이지 화면 지표와 같은 정의, 누적 31건 전부 일치)
  ipo    IPO 셋업             상장 후 250거래일 이내 & 종가가 첫날 시가 −10% 아래로 내려간 뒤 10거래일 이상 바닥을 다지고
                              아래에서 올라와 처음으로 첫날 시가의 95%에 도달한 날부터 5거래일 이내 & 현재 종가가 첫날 시가 95~110%
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

# 셋업별 유동성 기준 (시총 억, 직전 20일 평균 거래대금 억, 당일 거래대금 억). 스탁이지 교차검증에서
# '선 아래 종목을 스탁이지가 하나도 안 고른' 증거가 충분히 쌓인 셋업만 넣는다.
LIQ = {"lead": (2000, 30, 40), "trend": (300, 0, 0), "htf": (1500, 0, 0), "cup": (200, 0, 0)}

SETUPS = [
    ("gap", "거래량 동반 갭상승", "전일 고가 위로 갭을 띄우고 3% 이상 오르며 거래량이 50일 평균의 2배 이상"),
    ("lead", "RS 선두", "추세 템플릿을 충족하며 RS·1개월 RS 모두 75 이상, 52주 고점 근처의 시장 주도주 (시총 2,000억·20일 평균 거래대금 30억·당일 거래대금 40억 이상)"),
    ("trend", "추세 시작", "이동평균이 정배열로 막 바뀐 상승 2단계 초입, 52주 저점 대비 아직 덜 오른 종목 (시총 300억 이상)"),
    ("vcp", "VCP", "최근 40거래일 동안 2번 이상 수축(최대 20% 이상)하고 마지막 수축 10% 이하, 거래량이 직전 50일의 0.9배 이하로 마른 채 피봇 −6~+3% 자리"),
    ("cup", "컵위드핸들", "상승 추세(이평 정배열·200일선 상승) 속 10~25% 조정 컵을 85~110% 회복한 뒤 5~25일 핸들(깊이 20% 이내)에서 피봇 −10~+5% 자리 (RS 65·시총 200억 이상)"),
    ("htf", "HTF 급등 깃발", "8주 안에 50% 넘게 급등한 뒤 고점 대비 25% 이내에서 거래량·변동폭이 줄며 숨 고르는 종목 (RS 80·시총 1,500억 이상)"),
    ("ipo", "IPO 셋업", "상장 1년 이내, 첫날 시가 아래에서 바닥을 다진 뒤 처음으로 시가까지 올라온 종목"),
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


def _czz(c, th):
    """종가 지그재그: 종가가 직전 극점 대비 th 이상 되돌리면 그 극점을 확정. [(종류, 인덱스)]"""
    piv, trend, hi_i, lo_i = [], 0, 0, 0
    for i in range(1, len(c)):
        if c[i] > c[hi_i]:
            hi_i = i
        if c[i] < c[lo_i]:
            lo_i = i
        if trend >= 0 and c[i] <= c[hi_i] * (1 - th) and hi_i < i:
            piv.append(("H", hi_i)); trend, lo_i = -1, i
        elif trend <= 0 and c[i] >= c[lo_i] * (1 + th) and lo_i < i:
            piv.append(("L", lo_i)); trend, hi_i = 1, i
    return piv


def _vcp(hi, lo, c, v, t):
    """VCP(돌파 대기) — 스탁이지 화면 값 4건(수축 횟수·마지막 수축·피봇·거래량 수축)을 재현한 구조 (2026-11-26).

    파동: 종가 2.5% 지그재그로 고점·저점 확정. 피봇 = 마지막 확정 고점 구간(앞뒤 저점 사이)의 최고 고가.
    수축 깊이 = 피봇 이후 다음 고점 전까지 최저가 기준, 마지막 수축은 피봇 이후 오늘까지 최저가 기준.
    수축 횟수 = 피봇이 최근 40거래일 안에 있는 파동 수. 거래량 수축 = 최근 10일 평균 ÷ 그 전 50일 평균.
    조건: 수축 2회 이상 · 가장 깊은 수축 20% 이상 · 마지막 수축 10% 이하 · 피봇 대비 −6~+3% · 거래량 수축 0.9 이하.
    """
    if t < 60:
        return None
    w0 = max(0, t - 249)
    h, l, cc = hi[w0:t + 1], lo[w0:t + 1], c[w0:t + 1]
    T = len(cc) - 1
    piv = _czz(cc, 0.025)
    lows = [i for k, i in piv if k == "L"]
    waves = []
    for k, (typ, i) in enumerate(piv):
        if typ != "H":
            continue
        a = max([j for j in lows if j < i], default=0)
        b = min([j for j in lows if j > i], default=T)
        pi = a + int(np.argmax(h[a:b + 1]))
        nh = next((j for t2, j in piv[k + 1:] if t2 == "H"), None)
        e = nh if nh is not None else T
        waves.append((pi, h[pi], 1 - l[pi:e + 1].min() / h[pi]))
    if not waves:
        return None
    pi, pivot, _ = waves[-1]
    last = 1 - l[pi:].min() / pivot
    recent = [w for w in waves if w[0] >= T - 40]
    if len(recent) < 2:
        return None
    depths = [w[2] for w in recent[:-1]] + [last]
    if last > 0.10 or max(depths) < 0.20:
        return None
    gap = cc[T] / pivot - 1
    if not (-0.06 <= gap <= 0.03):
        return None
    base = v[t - 59:t - 9].mean()
    vr = v[t - 9:t + 1].mean() / base if base > 0 else np.nan
    if not (vr <= 0.9):
        return None
    return {"n": len(recent), "depths": [round(float(d) * 100, 1) for d in depths[-4:]], "pivot": float(pivot),
            "pgap": float(gap) * 100, "last": float(last) * 100, "vr": float(vr)}


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


def _cup_eval(hi, lo, c, t):
    """컵위드핸들 v2 (스탁이지 '컵위드핸들' 누적 17일 64건 역산, 재현율 64/64) → (결과 dict | None, 탈락 이유 | None).

    정의(스탁이지 화면 값 59/59건 일치):
      피봇 = 5거래일 이상 지난 고점의 고가. 이후 고가가 피봇을 최대 +8%까지 넘어도 피봇 유지.
      컵 조정폭 = 1 − (왼쪽 고점~피봇 사이 최저 저가 / 왼쪽 고점 고가), 회복률 = 종가 / 왼쪽 고점 고가,
      핸들 깊이 = 1 − (피봇 이후 최저 저가 / 피봇 고가).
    1) 추세: 200일선 ≥ 21일 전 200일선, 50일선 > 150·200일선, 종가 > 150일선, 150일선 > 200일선,
       종가 ≥ 200일선, 종가 ≥ 50일선×0.97, 종가 ≥ 52주 고가×0.8   (스탁이지 64건 전부 충족)
    2) 피봇 후보: 3% 지그재그 스윙 고점, 그다음 앞뒤 5일 최고 고가(5~25거래일 전) 순서로 보고, 통과한 후보 중 마지막 것
       (스탁이지 피봇과 62/62건 일치).
    3) 핸들: 깊이 ≤ 20%, 피봇 이후 고가 ≤ 피봇×1.08, 종가가 피봇 −10% ~ +5%.
    4) 컵: 피봇보다 8~260거래일 앞선 스윙 고점(직전 20일 최고)을 가까운 것부터 왼쪽 고점으로 시험.
       조정폭 10~25%, 왼쪽 고점→바닥·바닥→피봇 각 3거래일 이상, 회복률 85~110%, 피봇 ≥ 왼쪽 고점×0.9,
       핸들 저점이 컵 위쪽 절반, 왼쪽 고점 ≥ 52주 고가×0.75.
    RS ≥ 65 · 시총 ≥ 200억은 build() 에서 건다.
    """
    if t < 230:
        return None, "상장 230일 미만"
    ma = lambda n, k=t: c[k - n + 1:k + 1].mean()
    m50, m150, m200, m200b = ma(50), ma(150), ma(200), ma(200, t - 21)
    h52 = hi[max(0, t - 249):t + 1].max()
    for msg, ok in (("200일선 하락", m200 >= m200b), ("50일선<150·200일선", m50 > m150 and m50 > m200),
                    ("종가<150일선", c[t] > m150), ("150일선<200일선", m150 > m200), ("종가<200일선", c[t] >= m200),
                    ("종가<50일선×0.97", c[t] >= 0.97 * m50), ("종가<52주 고가×0.8", c[t] >= 0.8 * h52)):
        if not ok:
            return None, msg
    w0 = max(0, t - 380)
    H = [(i + w0, p) for k, i, p in _swings(hi[w0:t + 1], lo[w0:t + 1], 0.03) if k == "H"]
    cands = [(i, p) for i, p in H if 5 <= t - i <= 25]
    cands += [(i, hi[i]) for i in range(max(5, t - 25), t - 4) if hi[i] >= hi[i - 5:i + 6].max()]
    if not cands:
        return None, "피봇 후보(5~25일 전 고점) 없음"
    best, why = None, None
    for ip, pv in cands:
        hl = lo[ip + 1:t + 1].min()
        hd = 1 - hl / pv
        gap = c[t] / pv - 1
        if hd > 0.20:
            why = why or f"핸들 깊이 {hd * 100:.1f}% (>20%)"; continue
        if hi[ip + 1:t + 1].max() > pv * 1.08:
            why = why or f"피봇 {pv:,.0f} 이후 +8% 넘게 돌파"; continue
        if not (-0.10 <= gap <= 0.05):
            why = why or f"피봇 {pv:,.0f} 대비 {gap * 100:+.1f}% (허용 −10~+5%)"; continue
        for il, lp in reversed([x for x in H if x[0] < ip - 8]):
            if ip - il > 260:
                break
            if lp < hi[max(0, il - 20):il].max(initial=0):
                continue
            ib = il + int(np.argmin(lo[il:ip + 1]))
            b = lo[ib]
            dep, rec = 1 - b / lp, c[t] / lp
            if not (0.10 <= dep <= 0.25) or ib - il < 3 or ip - ib < 3:
                why = why or f"컵 조정폭 {dep * 100:.0f}% (10~25%)·기간"; continue
            if not (0.85 <= rec <= 1.10) or pv < lp * 0.9 or hl < b + 0.5 * (lp - b) or lp < 0.75 * h52:
                why = why or f"회복률 {rec * 100:.0f}% (85~110%)·핸들 위치·왼쪽 고점 높이"; continue
            if True:  # 통과한 후보 중 목록의 마지막(앞뒤 5일 최고 고가 후보 우선, 그중 가장 최근)
                best = {"depth": dep * 100, "rec": rec * 100, "pivot": pv, "pgap": gap * 100, "hdepth": hd * 100,
                        "rpk": (hi[ib:ip + 1].max() / pv - 1) * 100, "weeks": (ip - il) / 5, "hdays": t - ip}
            break
    if best is None:
        return None, why or "왼쪽 고점(8~260일 전 스윙 고점) 없음"
    return best, None


def _cup(hi, lo, c, t):
    return _cup_eval(hi, lo, c, t)[0]


def _ipo(o0, c, lo, t):
    """IPO 셋업 — 상장 첫날 시가 아래로 내려가 바닥을 다진 뒤, 아래에서 올라와 시가에 '처음' 다가온 종목.

    · 종가가 첫날 시가의 90% 아래로 내려간 적이 있어야 함(첫날 이후)
    · 그 뒤 처음으로 종가가 첫날 시가의 95% 이상에 도달한 날(첫 도달일)부터 5거래일 이내
    · 시가 아래에 머문 기간(처음 −10% 이탈 ~ 첫 도달) 10거래일 이상
    · 현재 종가가 첫날 시가의 95~110%
    """
    below = np.flatnonzero(c[1:t + 1] < o0 * 0.90)
    if not len(below):
        return None
    k = int(below[0]) + 1
    up = np.flatnonzero(c[k:t + 1] >= o0 * 0.95)
    if not len(up):
        return None
    f = k + int(up[0])
    if t - f > 4 or f - k < 10 or not (0.95 <= c[t] / o0 <= 1.10):
        return None
    return {"days": t + 1, "vsopen": (c[t] / o0 - 1) * 100, "open0": o0,
            "low": (lo[1:t + 1].min() / o0 - 1) * 100, "under": f - k, "fday": t - f}


def _htf(h, l, c, v, t, ma50):
    """HTF 급등 깃발 (High Tight Flag) — 스탁이지 '8주 상승률·고점 대비·거래량 수축·고저폭 수축'과 같은 정의로 계산.

    깃대: 최근 42거래일 최고가(고점) / 그 구간 시작~고점 사이 최저가 − 1 ≥ 50%
    깃발: 고점 이후 최저가 기준 고점 대비 하락 ≤ 25%, 종가 > 50일선. 고점 경과일 제한 없음(42일 창 안이면 됨 —
          스탁이지가 고점 20~22일 전 종목도 고름, 2026-11-26·27)
    수축: 최근 10일 평균 거래량 / 그 전 50일 평균 ≤ 1.10,
          최근 10일 평균 (고가−저가)/종가 / 그 전 60일 평균 ≤ 1.26
    (스탁이지 화면 수치와 대조: 상승률·고점 대비·거래량 수축은 일치, 고저폭 수축만 평균 0.03·최대 0.09 차이 —
     고가·저가 데이터 차이로 보임(거래량은 같음). 1.26 근처 종목은 판정이 갈릴 수 있음 — 2026-11-28)
    RS ≥ 80 와 시총 1,500억은 build() 에서 건다.
    """
    W = 42
    if t < 70:
        return None
    ip = t - W + 1 + int(np.argmax(h[t - W + 1:t + 1]))
    pk, age = h[ip], t - ip
    up = pk / l[t - W + 1:ip + 1].min() - 1
    pull = 1 - l[ip:t + 1].min() / pk
    vb = v[t - 59:t - 9].mean()
    rr = (h - l) / c
    rb = rr[t - 69:t - 9].mean()
    volc = v[t - 9:t + 1].mean() / vb if vb > 0 else np.nan
    rngc = rr[t - 9:t + 1].mean() / rb if rb > 0 else np.nan
    if not (up >= 0.50 and pull <= 0.25 and c[t] > ma50 and volc <= 1.10 and rngc <= 1.26):
        return None
    return {"up": up * 100, "pull": pull * 100, "age": int(age), "volc": volc, "rngc": rngc, "peak": pk}


def _r(v, nd=1):
    return None if v is None or not np.isfinite(v) else round(float(v), nd)


def build(con) -> dict:
    cal = con.execute(f"SELECT date FROM prices_daily WHERE symbol_id='KS11' ORDER BY date DESC LIMIT {HIST}").df()["date"]
    d0 = cal.min()
    px = con.execute("""
        SELECT p.symbol_id code, p.date, p.open, p.high, p.low, p.close,
               CASE WHEN p.volume > 0 THEN p.volume / p.adj_factor ELSE 0 END AS vol, p.value, p.mcap
        FROM v_prices_adj p JOIN v_rs_universe u USING (symbol_id)
        WHERE p.date >= ? AND p.close > 0 AND p.source IN ('marcap', 'naver', 'naver_fix', 'krx_cache', 'krx_daum', 'kiwoom')
        ORDER BY 1, 2""", [d0]).df()
    px["date"] = pd.to_datetime(px["date"]).dt.date
    first = con.execute("""SELECT p.symbol_id code, min(p.date) d FROM prices_daily p JOIN v_rs_universe u USING (symbol_id)
        WHERE p.source IN ('marcap', 'naver', 'naver_fix', 'krx_cache', 'krx_daum', 'kiwoom') GROUP BY 1""").df()
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
            mc_t = mc[t] / 1e8 if np.isfinite(mc[t]) else np.nan
            v20_t = val[max(0, t - 20):t].mean() / 1e8 if t >= 1 else np.nan
            res = {}
            res["gap"] = (o[t] > h[t - 1] and c[t] >= c[t - 1] * 1.03 and v50 > 0 and v[t] >= 2 * v50) and \
                {"gap": (o[t] / h[t - 1] - 1) * 100, "vx": v[t] / v50}
            res["lead"] = (mtt and r_rs >= 75 and r_1m >= 75 and c[t] >= 0.80 * h52[t]) and {"gap52": (c[t] / h52[t] - 1) * 100}
            res["trend"] = (mtt_px and c[t] >= 0.8 * h52[t] and c[t] <= 2 * l52[t]) and \
                {"gap52": (c[t] / h52[t] - 1) * 100, "upl": (c[t] / l52[t] - 1) * 100}
            vcp = _vcp(h, l, c, v, t) if mtt and t >= 60 else None
            res["vcp"] = vcp or False
            cup = _cup(h, l, c, t) if r_rs >= 65 else None
            res["cup"] = cup or False
            res["htf"] = (_htf(h, l, c, v, t, ma50[t]) if r_rs >= 80 else None) or False
            res["ipo"] = (_ipo(o[0], c, l, t) if listed_new and t <= 250 else None) or False
            if t >= 55:
                v5, base5 = v[t - 4:t + 1].sum(), v[t - 54:t - 4].sum() / 10
                r5 = c[t] / c[t - 5] - 1
                res["weekv"] = (base5 > 0 and v5 >= 2 * base5 and r5 >= 0.05) and {"vx": v5 / base5, "r5": r5 * 100}
            else:
                res["weekv"] = False
            for k, (cap, vmin, dmin) in LIQ.items():  # 셋업 정의에 포함된 유동성 기준 (값이 없으면 통과)
                if res[k] and ((np.isfinite(mc_t) and mc_t < cap) or (np.isfinite(v20_t) and v20_t < vmin)
                              or (np.isfinite(val[t]) and val[t] / 1e8 < dmin)):
                    res[k] = False
            for k in flags:
                flags[k].append(bool(res[k]))
            if day == last:
                info = {k: res[k] for k in res if res[k]}
                base = dict(close=c[t], chg=(c[t] / c[t - 1] - 1) * 100, value=val[t] / 1e8,
                            vratio=val[t] / val[max(0, t - 20):t].mean() if t >= 5 and val[max(0, t - 20):t].mean() > 0 else np.nan,
                            v20=v20_t, mcap=mc_t, rs=r_rs, rs1m=r_1m, mtt=bool(mtt))
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
