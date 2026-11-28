"""스탁이지 스크리너 결과 ↔ 우리 차트 셋업 교차검증.

사용법 (stockDB 폴더에서):
  PYTHONPATH=. python tools/xval_setups.py data/xval/stockeasy/2026-11-05.json [--db data/market.duckdb]

입력  data/xval/stockeasy/<날짜>.json  — 스탁이지 스크리너 프리셋별 종목 (code 포함)
출력  data/xval/ours/<날짜>.json         — 그날 우리 셋업 판정 결과 (시총·거래대금 필터 없이 전체)
      data/xval/report/<날짜>.md         — 일치/누락/추가 종목과 누락 이유
      data/xval/log.csv                   — 날짜·셋업·종목별 누적 기록 (같은 날짜를 다시 돌리면 덮어씀)
      data/xval/features/<날짜>.csv.gz    — 그날 전 종목 판정용 수치 (누적 학습용, tools/xval_fit.py 가 읽음)

비교 대상: 강세 선두↔lead, 추세 시작↔trend, 돌파 대기↔vcp, 컵위드핸들↔cup, HTF 급등 깃발↔htf, IPO 셋업↔ipo
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from src.dashboard import setup_export as S

MAP = {"강세 선두": "lead", "추세 시작": "trend", "돌파 대기": "vcp", "컵위드핸들": "cup", "HTF 급등 깃발": "htf", "IPO 셋업": "ipo"}
NAME = {v: k for k, v in MAP.items()}
ROOT = Path(".")
XV = ROOT / "data" / "xval"


def cup_diag(hi, lo, c, t):
    """setup_export._cup_eval(컵 v2)의 탈락 이유 (통과면 None)."""
    return S._cup_eval(hi, lo, c, t)[1]


def cup_candidates(hi, lo, c, t):
    """느슨한 컵 후보: 1~60일 전 확정 스윙 고점 각각을 피봇으로 보고, 가장 가까운 유효 왼쪽 고점과 짝지은 지표.
    (규칙 학습용 — 조건 없이 수치만 기록)"""
    w0 = max(0, t - 380)
    sw = [(k, i + w0, p) for k, i, p in S._swings(hi[w0:t + 1], lo[w0:t + 1])]
    out = []
    for _, ip, pivot in [x for x in sw if x[0] == "H" and t - 60 <= x[1] <= t - 1]:
        hl = lo[ip + 1:t + 1].min()
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
            if not (0.05 <= depth <= 0.5 and ib - il >= 5 and ip - ib >= 5):
                continue
            out.append({"age": int(t - ip), "pivot": float(pivot), "gap": round(float(c[t] / pivot - 1), 4),
                        "brk": round(float(hi[ip + 1:t + 1].max() / pivot - 1), 4), "hd": round(float(1 - hl / pivot), 4),
                        "depth": round(float(depth), 4), "rec": round(float(c[t] / lip), 4), "lip": float(lip),
                        "pocket": round(float(hi[ib:ip + 1].max() / pivot), 4), "weeks": round((ip - il) / 5, 1),
                        "hlow_pos": round(float((hl - bottom) / (lip - bottom)), 3) if lip > bottom else None})
            break
    return out


def evaluate(con, day: str) -> dict:
    con.execute("SET enable_progress_bar=false")
    d = pd.Timestamp(day).date()
    px = con.execute("""
        SELECT p.symbol_id code, p.date, p.open, p.high, p.low, p.close,
               CASE WHEN p.volume > 0 THEN p.volume / p.adj_factor ELSE 0 END vol, p.value, p.mcap
        FROM v_prices_adj p JOIN v_rs_universe u USING (symbol_id)
        WHERE p.date <= ? AND p.close > 0 AND p.source = 'kiwoom'
        ORDER BY 1, 2""", [d]).df()
    px["date"] = pd.to_datetime(px["date"]).dt.date
    first = px.groupby("code")["date"].min()
    db0 = first.min()
    rs = con.execute("SELECT symbol_id code, rs, rs_1m, rs_3m, rs_6m, rs_12m FROM rs_daily WHERE date = ?", [d]).df().set_index("code")
    names = dict(con.execute("SELECT id, name FROM symbols").fetchall())
    out = {}
    for code, g in px.groupby("code", sort=False):
        if g["date"].iloc[-1] != d or len(g) < 6:
            continue
        o, h, l, c = (g[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        v, val, mc = (g[k].to_numpy(float) for k in ("vol", "value", "mcap"))
        t = len(c) - 1
        if v[t] <= 0:
            continue
        R = float(rs.at[code, "rs"]) if code in rs.index and pd.notna(rs.at[code, "rs"]) else np.nan
        R1 = float(rs.at[code, "rs_1m"]) if code in rs.index and pd.notna(rs.at[code, "rs_1m"]) else np.nan
        ma50, ma150, ma200 = S._ma(c, 50)[t], S._ma(c, 150)[t], S._ma(c, 200)[t]
        ma200_1m = S._ma(c, 200)[t - 21] if t >= 221 else np.nan
        h52, l52 = h[max(0, t - 249):t + 1].max(), l[max(0, t - 249):t + 1].min()
        parts = {"상장 220일 이상": t >= 220, "종가>150일선": c[t] > ma150, "종가>200일선": c[t] > ma200, "150일선>200일선": ma150 > ma200,
                 "200일선 1개월 상승": ma200 > ma200_1m, "50일선>150·200일선": ma50 > ma150 and ma50 > ma200, "종가>50일선": c[t] > ma50,
                 "종가≥52주 저가×1.3": c[t] >= 1.3 * l52, "종가≥52주 고가×0.75": c[t] >= 0.75 * h52}
        mtt_px = all(parts.values())
        failed_px = [k for k, ok in parts.items() if not ok]
        mtt = mtt_px and R >= 70
        v50 = v[max(0, t - 50):t].mean() if t >= 20 else np.nan
        info = {"name": names.get(code, code), "close": c[t], "mcap": mc[t] / 1e8 if np.isfinite(mc[t]) else None,
                "v20": float(np.mean(val[max(0, t - 20):t])) / 1e8 if t else None, "rs": R, "rs1m": R1,
                "gap52": (c[t] / h52 - 1) * 100, "upl": (c[t] / l52 - 1) * 100}
        why = {}
        # lead
        f = failed_px + ([] if R >= 70 else [f"RS {R:.0f}<70"]) + ([] if R >= 85 else [f"RS {R:.0f}<85"]) \
            + ([] if R1 >= 85 else [f"RS1M {R1:.0f}<85"]) + ([] if c[t] >= 0.80 * h52 else [f"52주 고점 대비 {info['gap52']:.1f}% (<−20%)"])
        why["lead"] = f
        # trend
        why["trend"] = failed_px + ([] if c[t] >= 0.8 * h52 else [f"52주 고점 대비 {info['gap52']:.1f}% (<−20%)"]) \
            + ([] if c[t] <= 2 * l52 else [f"52주 저점 대비 +{info['upl']:.0f}% (>+100%)"]) + ([] if R1 >= 50 else [f"RS1M {R1:.0f}<50"])
        # vcp
        vf = failed_px + ([] if R >= 70 else [f"RS {R:.0f}<70"])
        if not vf and not S._vcp(h, l, c, v, t):
            vf.append("수축 패턴·거래량 수축 불충족")
        why["vcp"] = vf
        # cup
        cf = []
        if not R >= 65: cf.append(f"RS {R:.0f}<65")
        if not cf:
            r = cup_diag(h, l, c, t)
            if r: cf.append(r)
        why["cup"] = cf
        # htf (setup_export._htf 와 같은 정의)
        hf = {}
        if t >= 70:
            W = 42; ip = t - W + 1 + int(np.argmax(h[t - W + 1:t + 1])); pk = h[ip]
            rr = (h - l) / c; vb = v[t - 59:t - 9].mean(); rb = rr[t - 69:t - 9].mean()
            hf = {"up": pk / l[t - W + 1:ip + 1].min() - 1, "pull": 1 - l[ip:t + 1].min() / pk, "age": t - ip,
                  "volc": v[t - 9:t + 1].mean() / vb if vb > 0 else np.nan, "rngc": rr[t - 9:t + 1].mean() / rb if rb > 0 else np.nan}
            why["htf"] = [x for x, ok in [(f"8주 상승률 {hf['up'] * 100:.0f}% (<50%)", hf["up"] >= 0.50), (f"고점 대비 {hf['pull'] * 100:.1f}% (>25%)", hf["pull"] <= 0.25),
                                         ("종가<50일선", c[t] > ma50),
                                         (f"거래량 수축 {hf['volc']:.2f} (>1.10)", hf["volc"] <= 1.10), (f"고저폭 수축 {hf['rngc']:.2f} (>1.26)", hf["rngc"] <= 1.26),
                                         (f"RS {R:.0f}<85", R >= 85)] if not ok]
        else:
            why["htf"] = ["상장 70일 미만"]
        # ipo
        listed_new = first[code] > db0
        if not listed_new or t > 250:
            why["ipo"] = ["상장 1년 초과 또는 DB 이전 상장"]
        else:
            why["ipo"] = [] if S._ipo(o[0], c, l, t) else ["첫날 시가 아래에서 처음 도달 조건 불충족"]
        info["why"] = why
        pre = {s_: int(not why[s_]) for s_ in MAP.values()}  # 유동성 기준 적용 전 통과 여부
        for s_, (cap, vmin, *rest) in S.LIQ.items():  # 셋업 정의의 유동성 기준 (시총, 20일 대금, 당일 대금)
            dmin = rest[0] if rest else 0
            if info["mcap"] is not None and info["mcap"] < cap:
                why[s_] = why[s_] + [f"시총 {info['mcap']:.0f}억<{cap}"]
            if info["v20"] is not None and info["v20"] < vmin:
                why[s_] = why[s_] + [f"거래대금 {info['v20']:.1f}억<{vmin}"]
            if dmin and val[t] / 1e8 < dmin:
                why[s_] = why[s_] + [f"당일 거래대금 {val[t] / 1e8:.1f}억<{dmin}"]
        g3 = lambda k: float(rs.at[code, k]) if code in rs.index and pd.notna(rs.at[code, k]) else np.nan
        v10 = v[max(0, t - 9):t + 1].mean()
        rng = (h - l) / c
        vc = S._vcp(h, l, c, v, t) if t >= 60 else None
        feat = {"code": code, "name": info["name"], "mcap": info["mcap"], "v20": info["v20"], "val0": val[t] / 1e8, "chg": c[t] / c[t - 1] - 1,
                "rs": R, "rs1m": R1, "rs3m": g3("rs_3m"), "rs6m": g3("rs_6m"), "rs12m": g3("rs_12m"),
                "g52": c[t] / h52, "upl": c[t] / l52, "bars": t + 1, "mtt_px": int(mtt_px),
                **{f"p_{i}": int(bool(ok)) for i, ok in enumerate(parts.values())},
                "c_ma50": c[t] / ma50 if ma50 else np.nan, "c_ma200": c[t] / ma200 if ma200 else np.nan,
                "r40": c[t] / c[t - 40] - 1 if t >= 40 else np.nan, "dd8w": 1 - c[t] / h[max(0, t - 39):t + 1].max(),
                "vol10_50": v10 / v50 if v50 else np.nan,
                "rng10_50": rng[max(0, t - 9):t + 1].mean() / rng[max(0, t - 49):t + 1].mean() if t >= 50 else np.nan,
                "vcp_ok": int(vc is not None), "vcp_last": (vc["depths"][-1] if vc else np.nan), "vcp_pgap": (vc["pgap"] if vc else np.nan),
                "htf_up": hf.get("up", np.nan), "htf_pull": hf.get("pull", np.nan), "htf_age": hf.get("age", np.nan),
                "htf_volc": hf.get("volc", np.nan), "htf_rngc": hf.get("rngc", np.nan),
                "ipo_days": t + 1 if listed_new else np.nan, "ipo_vsopen": c[t] / o[0] - 1 if listed_new else np.nan,
                **{f"is_{s}": int(not why[s]) for s in MAP.values()},
                **{f"liq_{s}": pre[s] for s in MAP.values()},
                "cup_cands": json.dumps(cup_candidates(h, l, c, t)) if t >= 120 and c[t] > ma200 else "[]"}
        info["feat"] = feat
        out[code] = info
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stockeasy_json")
    ap.add_argument("--db", default="data/market.duckdb")
    a = ap.parse_args()
    se = json.load(open(a.stockeasy_json, encoding="utf-8"))
    day = se["date"]
    con = duckdb.connect(a.db, read_only=True)
    ev = evaluate(con, day)
    if not ev:
        raise SystemExit(f"DB에 {day} 키움 정규장 종가(source=kiwoom)가 없습니다. 클라우드 DB(cloud_state.py pull)를 다시 받으세요 — 다른 소스(KRX·네이버)는 쓰지 않습니다.")
    (XV / "ours").mkdir(parents=True, exist_ok=True)
    (XV / "report").mkdir(parents=True, exist_ok=True)
    ours = {s: sorted(c for c, i in ev.items() if not i["why"][s]) for s in MAP.values()}
    (XV / "features").mkdir(parents=True, exist_ok=True)
    pd.DataFrame([i["feat"] for i in ev.values()]).to_csv(XV / "features" / f"{day}.csv.gz", index=False)
    json.dump({"date": day, "setups": ours, "detail": {c: {k: v for k, v in i.items() if k != "feat"} for c, i in ev.items() if any(c in ours[s] for s in ours)}},
              open(XV / "ours" / f"{day}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    fmt = lambda x: "–" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:,.0f}"
    lines = [f"# 셋업 교차검증 {day}", "", "| 셋업 | 스탁이지 | 우리 | 일치 | 스탁이지만 | 우리만 | 재현율 |", "|---|---|---|---|---|---|---|"]
    detail, rows = [], []
    for kn, s in MAP.items():
        A = {r["code"] for r in se["presets"].get(kn, [])}
        B = set(ours[s])
        both, se_only, our_only = A & B, A - B, B - A
        rec = f"{len(both) / len(A) * 100:.0f}%" if A else "–"
        lines.append(f"| {kn} | {len(A)} | {len(B)} | {len(both)} | {len(se_only)} | {len(our_only)} | {rec} |")
        detail.append(f"\n## {kn} ({s})")
        if se_only:
            detail.append("**스탁이지에만 있음 — 우리 기준 탈락 이유**")
            for c in sorted(se_only):
                i = ev.get(c)
                detail.append(f"- {i['name'] if i else c} ({c}): " + ("; ".join(i["why"][s]) if i else "DB에 그날 데이터 없음"))
        if our_only:
            detail.append("**우리에만 있음** (시총·거래대금·RS·RS1M·52주 고점 대비)")
            for c in sorted(our_only, key=lambda c: -(ev[c]["mcap"] or 0)):
                i = ev[c]
                detail.append(f"- {i['name']} ({c}): {fmt(i['mcap'])}억 · {fmt(i['v20'])}억 · RS {fmt(i['rs'])} · {fmt(i['rs1m'])} · {i['gap52']:.1f}%")
        if both:
            detail.append("**일치**: " + ", ".join(ev[c]["name"] for c in sorted(both)))
        for c in sorted(A | B):
            i = ev.get(c, {})
            rows.append([day, kn, s, c, i.get("name", c), int(c in A), int(c in B),
                         "; ".join(i["why"][s]) if i and c in A - B else "",
                         round(i["mcap"]) if i.get("mcap") else "", round(i["v20"], 1) if i.get("v20") else "",
                         i.get("rs", ""), i.get("rs1m", "")])
    (XV / "report" / f"{day}.md").write_text("\n".join(lines + detail) + "\n", encoding="utf-8")
    log = XV / "log.csv"
    old = []
    if log.exists():
        with log.open(encoding="utf-8-sig") as f:
            old = [r for r in csv.reader(f)][1:]
        old = [r for r in old if r[0] != day]
    with log.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "preset", "setup", "code", "name", "stockeasy", "ours", "why_missed", "mcap_eok", "value20_eok", "rs", "rs1m"])
        w.writerows(old + rows)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
