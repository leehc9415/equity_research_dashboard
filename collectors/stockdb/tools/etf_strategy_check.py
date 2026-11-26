"""스탁이지 전략실 1호(모멘텀) ETF 규칙 재현 — Claude Code 이어받기용 기본 스크립트.

    py -3 tools\\etf_strategy_check.py

입력 (모두 stockDB 폴더 기준)
    data\\etf_analysis\\etf_daily_daum.csv.gz   ETF 416개 일봉 2024-10~2026-11 (다음 일별 시세, tradePrice = KRX 종가)
    data\\etf_analysis\\strategy_trades.csv     1호(신)·1호(구 관찰)·2호 매매 (전략실 화면에서 수집)
    data\\etf_analysis\\etf_screen_1109_1113.csv ETF 섹터 화면 5일치 (신호 색은 11/13만)
    data\\etf_pdf\\naver_top10_20261115.csv     ETF별 상위 10종목·비중 (네이버, 현재 시점)
    data\\etf_pdf\\<YYYYMMDD>.csv               pykrx 전체 PDF (tools\\etf_pdf_pykrx.py 로 수집, 있으면 우선 사용)

출력
    1) ETF 신호 색 규칙 일치율 (11/13 화면 대비)
    2) 1호 비손절 매도 vs '매수일 20일선 위 ETF 중 종목 비중 최대 ETF'의 첫 20일선 이탈일 일치율
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
A_DIR = ROOT / "data" / "etf_analysis"
PDF_DIR = ROOT / "data" / "etf_pdf"


def load_prices():
    e = pd.read_csv(A_DIR / "etf_daily_daum.csv.gz", dtype={"code": str}, parse_dates=["date"])
    c = e.pivot_table(index="date", columns="etf", values="c").sort_index()
    m20, m5 = c.rolling(20).mean(), c.rolling(5).mean()
    above = (c > m20) & m20.notna()
    # 신호 색: 초록 2 = 종가>20일선 & 5일선>20일선, 노랑 1 = 하나만, 빨강 0 = 둘 다 아래
    sig = pd.DataFrame(np.where(above & (m5 > m20), 2, np.where(above | (m5 > m20), 1, 0)),
                       index=c.index, columns=c.columns).where(m20.notna())
    gap = (c / m20 - 1) * 100
    return c, above, sig, gap


def check_signal_rule(sig, gap):
    s = pd.read_csv(A_DIR / "etf_screen_1109_1113.csv")
    s = s[(s.day == "2026-11-13") & s.sig.notna()]
    d = pd.Timestamp("2026-11-13")
    pred = s.etf.map(lambda e: {2: "G", 1: "Y", 0: "R"}.get(sig.at[d, e]) if e in sig.columns else None)
    ok = (pred == s.sig)
    print(f"[신호 색] 11/13 화면 {ok.notna().sum()}개 중 일치 {ok.mean():.1%}")


def load_weights():
    """종목별 소속 ETF와 비중. pykrx 전체 PDF가 있으면 그 ETF는 PDF, 나머지는 네이버 상위 10."""
    top = pd.read_csv(PDF_DIR / "naver_top10_20261115.csv", dtype={"code": str, "etf_code": str})
    w = top[["etf", "code", "weight"]]
    pdfs = sorted(p for p in PDF_DIR.glob("20*.csv"))
    if pdfs:
        latest = pd.read_csv(pdfs[-1], dtype={"code": str, "etf_code": str})
        latest = latest[latest.code.str.len() == 6]
        latest["weight"] = pd.to_numeric(latest.weight, errors="coerce")
        w = pd.concat([latest[["etf", "code", "weight"]], w[~w.etf.isin(latest.etf)]], ignore_index=True)
        print(f"[구성종목] pykrx PDF {pdfs[-1].name} ETF {latest.etf.nunique()}개 + 나머지 네이버 상위10")
    return w


def check_momentum_sells(above, w):
    t = pd.read_csv(A_DIR / "strategy_trades.csv", dtype={"code": str})
    t = t[(t.strat == "m_old") & t.sell_date.notna()].copy()
    t = t[t.ret > -7.9]                                  # 손절(−8%) 제외
    idx = above.index
    ok = pm1 = n = 0
    for _, r in t.iterrows():
        bd, sd = pd.Timestamp(r.buy_date), pd.Timestamp(r.sell_date)
        if bd not in idx or sd not in idx:
            continue
        n += 1
        h = w[(w.code == r.code) & w.etf.isin(above.columns)]
        h = h[[bool(above.at[bd, e]) for e in h.etf]] if len(h) else h
        if h.empty:
            continue
        e = h.sort_values("weight", ascending=False).etf.iloc[0]
        after = above[e].loc[bd:].iloc[1:]
        brk = after[~after]
        f = brk.index[0] if len(brk) else None
        ok += f == sd
        pm1 += f is not None and abs(idx.get_loc(f) - idx.get_loc(sd)) <= 1
    print(f"[1호 매도] 비손절 {n}건: 지배 ETF 첫 20일선 이탈 = 매도일 {ok / n:.1%} (±1일 {pm1 / n:.1%})")


if __name__ == "__main__":
    c, above, sig, gap = load_prices()
    check_signal_rule(sig, gap)
    check_momentum_sells(above, load_weights())
