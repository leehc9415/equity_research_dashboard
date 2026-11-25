"""누적 교차검증 데이터로 스탁이지 셋업 조건을 추정한다.

사용법 (stockDB 폴더에서):  python tools/xval_fit.py
입력  data/xval/stockeasy/*.json (스탁이지 결과)  +  data/xval/features/*.csv.gz (그날 전 종목 수치)
출력  data/xval/fit/<마지막 날짜>.md

방법 (셋업마다)
  1) 포함 범위: 지금까지 쌓인 스탁이지 종목 '전부'를 담는 가장 좁은 조건 (각 지표의 최소·최대, 항상 참인 조건)
     → 보기 좋은 값으로 바깥쪽 반올림해 '제안 기준'으로 낸다.
  2) 그 범위 안인데 스탁이지가 고르지 않은 종목 = 아직 설명 못 한 차이 (날짜별 개수, 예시)
  3) 검증: 마지막 날을 빼고 만든 범위로 마지막 날 스탁이지 종목을 몇 개 잡는지 (과적합 확인)
  4) 지금 로직(setup_export)의 누적 재현율·종목 수와 비교
"""
from __future__ import annotations

import glob
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

XV = Path("data/xval")
PRESET = {"강세 선두": "lead", "추세 시작": "trend", "컵위드핸들": "cup", "HTF 급등 깃발": "htf", "돌파 대기": "vcp", "IPO 셋업": "ipo"}
MTT_PARTS = ["상장 220일 이상", "종가>150일선", "종가>200일선", "150일선>200일선", "200일선 1개월 상승", "50일선>150·200일선",
             "종가>50일선", "종가≥52주 저가×1.3", "종가≥52주 고가×0.75"]
# 셋업별로 볼 지표: (컬럼, 하한 사용, 상한 사용, 표시 이름, 반올림 단위)
FEATS = {
    "lead": [("rs", 1, 0, "RS", 5), ("rs1m", 1, 0, "RS 1M", 5), ("g52", 1, 0, "종가/52주 고가", 0.01), ("upl", 0, 1, "종가/52주 저가", 0.1),
             ("mcap", 1, 0, "시총(억)", 100), ("v20", 1, 0, "20일 평균 거래대금(억)", 1)],
    "trend": [("rs", 1, 0, "RS", 5), ("rs1m", 1, 0, "RS 1M", 5), ("g52", 1, 0, "종가/52주 고가", 0.01), ("upl", 1, 1, "종가/52주 저가", 0.1),
              ("mcap", 1, 0, "시총(억)", 100), ("v20", 1, 0, "20일 평균 거래대금(억)", 1)],
}
FEATS["htf"] = [("htf_up", 1, 0, "8주 상승률", 0.05), ("htf_pull", 0, 1, "고점 대비 하락", 0.01), ("htf_age", 1, 1, "고점 후 경과일", 1),
                ("htf_volc", 0, 1, "거래량 수축(10/60일)", 0.05), ("htf_rngc", 0, 1, "고저폭 수축(5/20일)", 0.05), ("c_ma50", 1, 0, "종가/50일선", 0.01),
                ("rs", 1, 0, "RS", 5), ("mcap", 1, 0, "시총(억)", 100), ("v20", 1, 0, "20일 평균 거래대금(억)", 1)]
CUP_ATTR = [("age", 1, 1, "피봇 경과일", 1), ("gap", 1, 1, "피봇 대비", 0.01), ("brk", 0, 1, "피봇 이후 최고 돌파폭", 0.01),
            ("hd", 0, 1, "핸들 깊이", 0.01), ("depth", 1, 1, "컵 조정폭", 0.01), ("rec", 1, 1, "회복률(현재가/왼쪽 고점)", 0.01),
            ("pocket", 1, 0, "오른쪽 고점/피봇", 0.01), ("hlow_pos", 1, 0, "핸들 저점 위치(컵 안 0~1)", 0.05)]
CUP_STOCK = [("rs", 1, 0, "RS", 5), ("mcap", 1, 0, "시총(억)", 100), ("v20", 1, 0, "20일 평균 거래대금(억)", 1),
             ("c_ma50", 1, 0, "종가/50일선", 0.01), ("c_ma200", 1, 0, "종가/200일선", 0.01)]


def nice(v, step, lower):
    if v is None or not np.isfinite(v):
        return None
    f = math.floor if lower else math.ceil
    return round(f(v / step) * step, 6)


def load():
    se, fe = {}, {}
    for f in sorted(glob.glob(str(XV / "stockeasy" / "*.json"))):
        j = json.load(open(f, encoding="utf-8"))
        d = j["date"]
        if not (XV / "features" / f"{d}.csv.gz").exists():
            continue
        se[d] = {PRESET[k]: v for k, v in j["presets"].items() if k in PRESET}
        fe[d] = pd.read_csv(XV / "features" / f"{d}.csv.gz", dtype={"code": str}).set_index("code")
    return se, fe


def envelope(P: pd.DataFrame, feats):
    rule = {}
    for col, lo, hi, _, step in feats:
        x = P[col].dropna()
        if x.empty:
            continue
        rule[col] = (nice(x.min(), step, True) if lo else None, nice(x.max(), step, False) if hi else None, x.min(), x.max())
    return rule


def inside(df, rule, bools):
    m = pd.Series(True, index=df.index)
    for col, (lo, hi, *_r) in rule.items():
        if lo is not None:
            m &= df[col] >= lo
        if hi is not None:
            m &= df[col] <= hi
    for b in bools:
        m &= df[b] == 1
    return m


def fit_simple(s, se, fe, days, out):
    feats = FEATS[s]
    P = pd.concat([fe[d].loc[[r["code"] for r in se[d].get(s, []) if r["code"] in fe[d].index]].assign(day=d) for d in days])
    if P.empty:
        out.append(f"\n## {s}: 스탁이지 종목이 아직 없음"); return
    bools = [f"p_{i}" for i in range(len(MTT_PARTS)) if P[f"p_{i}"].min() == 1] if s != "htf" else []
    rule = envelope(P, feats)
    out.append(f"\n## {s} — 스탁이지 종목 {len(P)}건 ({len(days)}일)")
    out.append("**항상 참인 추세 조건**: " + (", ".join(MTT_PARTS[int(b[2:])] for b in bools) or "없음"))
    out.append("| 지표 | 스탁이지 종목 범위 | 제안 기준 |\n|---|---|---|")
    for col, lo, hi, nm, _ in feats:
        if col in rule:
            a, b, mn, mx = rule[col]
            prop = " · ".join(x for x in [f"≥ {a:g}" if a is not None else "", f"≤ {b:g}" if b is not None else ""] if x)
            out.append(f"| {nm} | {mn:.3g} ~ {mx:.3g} | {prop} |")
    tot_in = tot_cur = 0; lines = []
    for d in days:
        df = fe[d]; A = {r["code"] for r in se[d].get(s, [])}
        m = inside(df, rule, bools); B = set(df.index[m]); C = set(df.index[df[f"is_{s}"] == 1])
        tot_in += len(B); tot_cur += len(C)
        extra = df.loc[list(B - A)].sort_values("mcap", ascending=False)
        lines.append(f"| {d} | {len(A)} | {len(A & B)} | {len(B - A)} | {len(A & C)} | {len(C)} | "
                     + ", ".join(f"{r['name']}" for _, r in extra.head(6).iterrows()) + " |")
    out.append("\n| 날짜 | 스탁이지 | 제안 기준 일치 | 제안 기준 추가 | 지금 로직 일치 | 지금 로직 종목 수 | 추가 종목 예 (시총순) |\n|---|---|---|---|---|---|---|")
    out += lines
    if len(days) >= 2:
        prev = days[:-1]; Pp = P[P.day.isin(prev)]
        rp = envelope(Pp, feats); bp = [f"p_{i}" for i in range(len(MTT_PARTS)) if Pp[f"p_{i}"].min() == 1] if s != "htf" else []
        d = days[-1]; A = {r["code"] for r in se[d].get(s, [])}; B = set(fe[d].index[inside(fe[d], rp, bp)])
        miss = [fe[d].at[c, "name"] if c in fe[d].index else c for c in A - B]
        out.append(f"\n검증: {', '.join(prev)} 로 만든 기준 → {d} 스탁이지 {len(A)}개 중 {len(A & B)}개 포함" + (f" (놓침: {', '.join(miss)})" if miss else ""))


def fit_cup(se, fe, days, out):
    rows = []
    for d in days:
        df = fe[d]
        for r in se[d].get("cup", []):
            c = r["code"]
            if c not in df.index:
                continue
            cands = json.loads(df.at[c, "cup_cands"]) if isinstance(df.at[c, "cup_cands"], str) else []
            sp = r.get("피봇(돌파가)")
            sp = float(str(sp).replace(",", "")) if sp else None
            best = None
            if cands:
                best = min(cands, key=lambda k: abs(k["pivot"] / sp - 1)) if sp else None
            rows.append({"day": d, "code": c, "name": df.at[c, "name"], "se_pivot": sp, "best": best, "n": len(cands),
                         **{k: df.at[c, k] for k, *_ in CUP_STOCK}})
    out.append(f"\n## cup — 스탁이지 종목 {len(rows)}건")
    if not rows:
        return
    out.append("| 날짜 | 종목 | 스탁이지 피봇 | 가장 가까운 우리 후보 | 경과일 | 피봇 대비 | 돌파폭 | 핸들 | 조정폭 | 회복률 | 오른쪽 고점/피봇 |\n|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        b = r["best"]
        if b:
            out.append(f"| {r['day']} | {r['name']} | {r['se_pivot'] or '–'} | {b['pivot']:,.0f} ({(b['pivot'] / r['se_pivot'] - 1) * 100:+.1f}%) | {b['age']} | {b['gap'] * 100:+.1f}% | "
                       f"{b['brk'] * 100:+.1f}% | {b['hd'] * 100:.1f}% | {b['depth'] * 100:.0f}% | {b['rec'] * 100:.0f}% | {b['pocket']:.3f} |" if r["se_pivot"] else
                       f"| {r['day']} | {r['name']} | – | 후보 {r['n']}개 (스탁이지 피봇 미기록) | | | | | | | |")
        else:
            out.append(f"| {r['day']} | {r['name']} | {r['se_pivot'] or '–'} | 후보 없음 | | | | | | | |")
    B = pd.DataFrame([dict(r["best"], **{k: r[k] for k, *_ in CUP_STOCK}) for r in rows if r["best"]])
    if B.empty:
        return
    ra = envelope(B, CUP_ATTR); rs_ = envelope(B, CUP_STOCK)
    out.append("\n| 지표 | 스탁이지 종목 범위 | 제안 기준 |\n|---|---|---|")
    for col, lo, hi, nm, _ in CUP_ATTR + CUP_STOCK:
        rr = ra.get(col) or rs_.get(col)
        if rr:
            a, b, mn, mx = rr
            out.append(f"| {nm} | {mn:.3g} ~ {mx:.3g} | " + " · ".join(x for x in [f"≥ {a:g}" if a is not None else "", f"≤ {b:g}" if b is not None else ""] if x) + " |")
    out.append("\n| 날짜 | 스탁이지 | 제안 기준 일치 | 제안 기준 추가 | 지금 로직 일치 | 지금 로직 종목 수 |\n|---|---|---|---|---|---|")
    for d in days:
        df = fe[d]; A = {r["code"] for r in se[d].get("cup", [])}
        ms = inside(df, rs_, [])
        def ok(js):
            for k in json.loads(js) if isinstance(js, str) else []:
                if all((lo is None or (k.get(c) is not None and k[c] >= lo)) and (hi is None or (k.get(c) is not None and k[c] <= hi)) for c, (lo, hi, *_r) in ra.items()):
                    return True
            return False
        B2 = set(df.index[ms & df["cup_cands"].apply(ok)]); C = set(df.index[df["is_cup"] == 1])
        out.append(f"| {d} | {len(A)} | {len(A & B2)} | {len(B2 - A)} | {len(A & C)} | {len(C)} |")


def fit_liq(se, fe, days, out):
    """셋업별 유동성 필터 증거: 셋업 가격 조건(유동성 제외)은 통과했는데 선 아래라서 스탁이지가 0건이면 필터 증거.
    선 아래 우리 종목이 많고 그중 스탁이지가 0건일수록 강한 증거. 기준선은 스탁이지 최솟값보다 아래의 딱 떨어지는 값으로만 잡는다."""
    out.append("\n## 셋업별 유동성 필터 증거 (누적)")
    out.append("우리 판정 = 유동성 기준을 빼고 본 셋업 통과 종목. '선 아래 n → 스탁이지 k' 에서 n이 크고 k=0 이면 그 선 근처에 필터가 있다는 증거 (권장: n≥20, 2주 이상 누적 후 반영).")
    out.append("\n| 셋업 | 스탁이지 누적 | 스탁이지 최소 시총 | 최소 거래대금 | 시총 300/500/1000/2000억 아래 (n→k) | 거래대금 5/10/20/30억 아래 (n→k) |")
    out.append("|---|---|---|---|---|---|")
    for s in ("lead", "trend", "cup", "htf", "vcp", "ipo"):
        rows = []
        for d in days:
            df = fe[d]; codes = {r["code"] for r in se[d].get(s, [])}
            col = f"liq_{s}" if f"liq_{s}" in df else f"is_{s}"
            if col not in df:
                continue
            x = df[df[col] == 1][["mcap", "v20"]].copy(); x["se"] = x.index.isin(codes)
            sx = df.loc[df.index.isin(codes), ["mcap", "v20"]].copy(); sx["se"] = True
            rows.append(pd.concat([x, sx[~sx.index.isin(x.index)]]))
        if not rows:
            continue
        O = pd.concat(rows); P = O[O.se]
        if P.empty:
            out.append(f"| {s} | 0 | – | – | – | – |"); continue
        cap = " · ".join(f"{(O.mcap < c).sum()}→{int(O[O.mcap < c].se.sum())}" for c in (300, 500, 1000, 2000))
        val = " · ".join(f"{(O.v20 < c).sum()}→{int(O[O.v20 < c].se.sum())}" for c in (5, 10, 20, 30))
        out.append(f"| {s} | {len(P)} | {P.mcap.min():,.0f}억 | {P.v20.min():.1f}억 | {cap} | {val} |")


def main():
    se, fe = load()
    days = sorted(se)
    if not days:
        raise SystemExit("features 가 있는 날짜가 없습니다 (먼저 tools/xval_setups.py 실행)")
    out = [f"# 스탁이지 조건 추정 — 누적 {len(days)}일 ({days[0]} ~ {days[-1]})",
           "제안 기준 = 지금까지 스탁이지 종목을 모두 포함하는 가장 좁은 범위(보기 좋은 값으로 바깥쪽 반올림). "
           "'추가'는 그 범위 안인데 스탁이지가 고르지 않은 종목 — 날짜가 쌓일수록 줄여 나갈 대상."]
    for s in ("lead", "trend", "htf"):
        fit_simple(s, se, fe, days, out)
    fit_cup(se, fe, days, out)
    fit_liq(se, fe, days, out)
    for s in ("vcp", "ipo"):
        n = sum(len(se[d].get(s, [])) for d in days)
        out.append(f"\n## {s}: 스탁이지 종목 누적 {n}건" + (" — 아직 추정 불가" if n == 0 else ""))
    (XV / "fit").mkdir(parents=True, exist_ok=True)
    (XV / "fit" / f"{days[-1]}.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
