"""스탁이지 전략실 1호·2호 — 고정 규칙으로 그날 편입·편출을 미리 판정하고, 실제 화면 기록과 채점한다.

규칙은 이 파일에 고정해 두고(날마다 데이터에 맞추지 않음) 매일 장마감 뒤 돌린다 → 표본 밖 검증.
시세는 키움 정규장(클라우드 DB v_prices_adj, source='kiwoom')만 쓴다. ETF도 같은 DB의 키움 일봉(수정주가).
ETF↔종목 연결은 스탁이지 ETF 화면의 대표종목 5개(data/xval/etf/*.json 누적 합집합)만 쓴다(구성종목 전체 없음).

1호 규칙 v1 (2026-11-28, 1호(구) 거래 361건 역산)
  매수: 대표종목으로 담은 ETF 중 신호 G(종가>20일선 & 5일선>20일선)가 1개 이상 · 당일 상승 · 양봉 · 종가>20일선
        · RS 1M ≥ 75 · 종가가 20일선 위로 올라온 지 20거래일 이내 · G ETF 중 가장 최근 20일선 돌파 ETF의 포지션 ≤ 10일
        (재현 91%, 후보 하루 약 18개). 정렬: G ETF가 그날 직전 20일 최고 종가 돌파(etf_break20) → G ETF 수 → 거래대금
        (돌파 조건까지 걸면 재현 76%·하루 약 11개)
  매도: 손절 = 저가 ≤ 매수가×0.92 / 기준 ETF(그 종목 순위가 가장 높은 ETF, 같으면 포지션 짧은 것)가 그날 20일선 아래로 이탈
2호 규칙 v1
  매수: 종가 > 직전 250거래일 최고 종가 · 상승 · 양봉 · 등락률 < +29.5% · 거래대금 ≥ 50억 · RS 1M ≥ 80 (빈 자리 = 20 − 보유)
  매도: 손절 저가 ≤ 매수가×0.92 / 종가 < 20일선

사용: PYTHONPATH=. python tools/xval_predict.py YYYY-MM-DD   (strategy/<날짜>.json 이 있으면 채점까지)
출력: data/xval/predict/<날짜>.json, data/xval/predict/score.csv (날짜 단위로 덮어쓰기·누적)
"""
from __future__ import annotations

import csv
import glob
import json
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

XV = Path("data/xval")
DB = Path("data/market.duckdb")


def load(con, day):
    d = pd.Timestamp(day)
    names = set()
    mem: dict[str, dict[str, int]] = {}
    for f in sorted(glob.glob(str(XV / "etf" / "*.json"))):
        if Path(f).stem > day:
            continue
        for r in json.load(open(f, encoding="utf-8"))["rows"]:
            names.add(r["etf"])
            for i, h in enumerate(r["holdings"]):
                cur = mem.setdefault(h["name"], {})
                cur[r["etf"]] = min(cur.get(r["etf"], 99), i + 1)
    ecode = dict(con.execute("SELECT name, max(code) FROM kiwoom_listing WHERE name IN (SELECT unnest(?)) GROUP BY 1",
                             [list(names)]).fetchall())
    e = con.execute("""SELECT symbol_id code, date, close FROM v_prices_adj WHERE source='kiwoom' AND date <= ? AND date >= ?
                       AND symbol_id IN (SELECT unnest(?))""", [d.date(), (d - pd.Timedelta(days=200)).date(), list(ecode.values())]).df()
    e["date"] = pd.to_datetime(e["date"])
    E = e.pivot(index="date", columns="code", values="close").sort_index()
    px = con.execute("""SELECT p.symbol_id code, p.date, p.open, p.high, p.low, p.close, p.value
                        FROM v_prices_adj p JOIN v_rs_universe u USING (symbol_id)
                        WHERE p.source='kiwoom' AND p.date <= ? AND p.date >= ?""", [d.date(), (d - pd.Timedelta(days=420)).date()]).df()
    px["date"] = pd.to_datetime(px["date"])
    S = {k: px.pivot(index="date", columns="code", values=k).sort_index() for k in ("open", "high", "low", "close", "value")}
    rs = con.execute("SELECT symbol_id code, rs, rs_1m FROM rs_daily WHERE date = ?", [d.date()]).df().set_index("code")
    nm = dict(con.execute("SELECT id, name FROM symbols WHERE length(id)=6").fetchall())
    return d, mem, ecode, E, S, rs, nm


def etf_state(E):
    m20, m5 = E.rolling(20).mean(), E.rolling(5).mean()
    above = E > m20
    sig = pd.DataFrame(np.where(above & (m5 > m20), "G", np.where(above | (m5 > m20), "Y", "R")), index=E.index, columns=E.columns)
    pos = pd.DataFrame(index=E.index, columns=E.columns, dtype=float)
    for c in E.columns:
        a, cnt, out = above[c].to_numpy(), 0, []
        for v in a:
            cnt = (cnt + 1 if cnt > 0 else 1) if v else (cnt - 1 if cnt < 0 else -1)
            out.append(cnt)
        pos[c] = out
    return sig, pos, m20


def predict(day):
    con = duckdb.connect(str(DB), read_only=True)
    d, mem, ecode, E, S, rs, nm = load(con, day)
    if d not in S["close"].index:
        raise SystemExit(f"{day} 키움 시세 없음")
    sig, pos, em20 = etf_state(E)
    ehi20 = E.shift(1).rolling(20).max()
    O, Hh, L, C, V = S["open"], S["high"], S["low"], S["close"], S["value"]
    i = C.index.get_loc(d)
    ma20 = C.rolling(20).mean()
    above = (C > ma20)
    name2code = {v: k for k, v in nm.items()}

    def etfs(name):
        return [(ecode[e], e, rk) for e, rk in mem.get(name, {}).items() if e in ecode and ecode[e] in E.columns]

    # 1호 매수 후보
    buy1 = []
    for name in mem:
        cd = name2code.get(name)
        if cd not in C.columns or not np.isfinite(C.at[d, cd]):
            continue
        c0, p0, o0 = C.at[d, cd], C[cd].iloc[i - 1], O.at[d, cd]
        if not (c0 > p0 and c0 > o0 and c0 > ma20.at[d, cd]):
            continue
        r1 = rs.at[cd, "rs_1m"] if cd in rs.index else np.nan
        if not r1 >= 75:
            continue
        ab = above[cd].iloc[: i + 1].to_numpy()[::-1]
        days_above = int(np.argmin(ab)) if not ab.all() else len(ab)
        G = [(c, e, rk) for c, e, rk in etfs(name) if sig.at[d, c] == "G"]
        if not G or days_above > 20:
            continue
        minpos = min(pos.at[d, c] for c, _, _ in G)
        brk = [e for c, e, _ in G if E.at[d, c] > ehi20.at[d, c]]
        if minpos > 10:
            continue
        buy1.append({"code": cd, "name": name, "chg": round((c0 / p0 - 1) * 100, 2), "rs1m": float(r1), "days_above20": days_above,
                     "nG": len(G), "value_eok": round(V.at[d, cd] / 1e8, 1), "etf_break20": brk[:3]})
    buy1.sort(key=lambda x: (-bool(x["etf_break20"]), -x["nG"], -x["value_eok"]))

    strat_f = XV / "strategy" / f"{day}.json"
    strat = json.load(open(strat_f, encoding="utf-8")) if strat_f.exists() else None
    prev = sorted(Path(XV / "strategy").glob("*.json"))
    prev = [p for p in prev if p.stem < day]
    prev = json.load(open(prev[-1], encoding="utf-8")) if prev else None

    def sells1(hold):
        out = []
        for h in hold:
            cd = name2code.get(h["name"])
            if cd not in C.columns:
                continue
            why = None
            if L.at[d, cd] <= h["buy_px"] * 0.92:
                why = "손절 −8%"
            else:
                es = etfs(h["name"])
                if es:
                    es.sort(key=lambda x: (x[2], pos.at[d, x[0]] if pos.at[d, x[0]] > 0 else 999))
                    c, e, _ = es[0]
                    if E.at[d, c] < em20.at[d, c] and E[c].iloc[-2] >= em20[c].iloc[-2]:
                        why = f"기준 ETF {e} 20일선 이탈"
            if why:
                out.append({"name": h["name"], "why": why})
        return out

    def sells2(hold):
        out = []
        for h in hold:
            cd = name2code.get(h["name"])
            if cd not in C.columns:
                continue
            if L.at[d, cd] <= h["buy_px"] * 0.92:
                out.append({"name": h["name"], "why": "손절 −8%"})
            elif C.at[d, cd] < ma20.at[d, cd]:
                out.append({"name": h["name"], "why": "종가<20일선"})
        return out

    hi250 = C.shift(1).rolling(250, min_periods=200).max()
    c0, p0 = C.loc[d], C.iloc[i - 1]
    m = (c0 > hi250.loc[d]) & (c0 > p0) & (c0 > O.loc[d]) & (c0 < p0 * 1.295) & (V.loc[d] >= 50e8)
    buy2 = []
    for cd in m[m].index:
        r1 = rs.at[cd, "rs_1m"] if cd in rs.index else np.nan
        if r1 >= 80:
            buy2.append({"code": cd, "name": nm.get(cd), "chg": round((c0[cd] / p0[cd] - 1) * 100, 2), "rs1m": float(r1),
                         "value_eok": round(V.at[d, cd] / 1e8, 1)})
    buy2.sort(key=lambda x: -x["value_eok"])

    res = {"date": day, "rules": "v1 (2026-11-28)", "momentum": {"buy_candidates": buy1}, "peak": {"buy_candidates": buy2}}
    if prev:
        res["momentum"]["sell_pred"] = sells1(prev["momentum_observe"]["holdings"])
        res["peak"]["sell_pred"] = sells2(prev["peak"]["holdings"])
    score = {}
    if strat and prev:
        for key, pk in (("momentum", "momentum_observe"), ("peak", "peak")):
            old = {h["name"] for h in prev[pk]["holdings"]}
            new = {h["name"] for h in strat[pk]["holdings"]}
            bought, sold = sorted(new - old), sorted(old - new)
            pb = {x["name"] for x in res[key]["buy_candidates"]}
            ps = {x["name"] for x in res[key].get("sell_pred", [])}
            score[key] = {"bought": bought, "bought_in_candidates": sorted(set(bought) & pb), "n_candidates": len(pb),
                          "sold": sold, "sold_predicted": sorted(set(sold) & ps), "predicted_not_sold": sorted(ps - set(sold))}
        res["score"] = score
    out = XV / "predict"
    out.mkdir(parents=True, exist_ok=True)
    json.dump(res, open(out / f"{day}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if score:
        f = out / "score.csv"
        rows = [r for r in csv.reader(open(f, encoding="utf-8"))] if f.exists() else [["date", "strategy", "bought", "bought_hit", "n_cand", "sold", "sold_hit", "false_sell"]]
        rows = [r for r in rows if r[0] != day]
        for k, v in score.items():
            rows.append([day, k, len(v["bought"]), len(v["bought_in_candidates"]), v["n_candidates"], len(v["sold"]), len(v["sold_predicted"]), len(v["predicted_not_sold"])])
        csv.writer(open(f, "w", encoding="utf-8", newline="")).writerows(rows)
    return res


if __name__ == "__main__":
    r = predict(sys.argv[1])
    print(json.dumps(r.get("score", {}), ensure_ascii=False, indent=1))
    print("1호 후보", len(r["momentum"]["buy_candidates"]), "2호 후보", len(r["peak"]["buy_candidates"]))
