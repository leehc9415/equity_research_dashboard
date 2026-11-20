"""raw/*.json → macro-data.json / macro-data.js (보드 타일 + 지표 브리핑 블록).

원칙: 숫자는 모두 여기서 계산하고, 화면(JS)은 블록을 그리기만 한다.
요약 문장도 수치에서 템플릿으로 생성한다(추후 LLM 해석을 붙일 자리는 'commentary').
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))


# ─────────────────────────── 날짜·수치 도우미
def add_months(d: str, n: int) -> str:
    y, m = int(d[:4]), int(d[5:7])
    k = y * 12 + (m - 1) + n
    return f"{k // 12:04d}-{k % 12 + 1:02d}-01"


def ym(d: str) -> str:
    return f"{d[:4]}.{d[5:7]}"


def ko_month(d: str) -> str:
    return f"{int(d[:4])}년 {int(d[5:7])}월"


def freq_of(obs) -> str:
    if len(obs) < 3:
        return "A"
    ds = [dt.date.fromisoformat(o[0]) for o in obs[-12:]]
    gap = statistics.median((b - a).days for a, b in zip(ds, ds[1:]))
    return "D" if gap <= 4 else "W" if gap <= 8 else "M" if gap <= 40 else "Q" if gap <= 120 else "A"


def sgn(x, d=1, unit=""):
    if x is None:
        return "–"
    s = f"{x:+.{d}f}"
    return s + unit


def word(actual, cons, d=1):
    if cons is None or actual is None:
        return ""
    a, c = round(actual, d), round(cons, d)
    return "상회" if a > c else "하회" if a < c else "부합"


class Data:
    def __init__(self, raw: Path, out: Path):
        self.fred = json.loads((raw / "fred.json").read_text())
        self.ecos = json.loads((raw / "ecos.json").read_text()) if (raw / "ecos.json").exists() else {}
        self.alfred = json.loads((raw / "alfred.json").read_text()) if (raw / "alfred.json").exists() else {}
        self.fomc = json.loads((raw / "fomc.json").read_text()) if (raw / "fomc.json").exists() else []
        self.kalshi = json.loads((raw / "kalshi.json").read_text()) if (raw / "kalshi.json").exists() else []
        self.ff = json.loads((raw / "ff_thisweek.json").read_text()) if (raw / "ff_thisweek.json").exists() else []
        self.exports = json.loads((raw / "exports.json").read_text()) if (raw / "exports.json").exists() else {}
        cons = {}
        # 우선순위: 수동 < ForexFactory 주간 스냅샷 < Investing.com (뒤에서 덮어씀)
        for f in ("consensus_manual.json", "consensus_archive.json"):
            p = out / f
            if p.exists():
                cons.update({k: v for k, v in json.loads(p.read_text()).items() if not k.startswith("_")})
        p = raw / "investing.json"
        self.inv = json.loads(p.read_text()) if p.exists() else {"events": {}}
        for eid, ev in self.inv.get("events", {}).items():
            m = C.INVESTING_EVENTS.get(int(eid))
            if not m:
                continue
            key, _, mult = m
            occ = list(ev.get("occurrences", [])) + ([ev["next"]] if ev.get("next") else [])
            for o in occ:
                if o.get("forecast") is None:
                    continue
                cons[f"{o['occurrence_time'][:10]}|{key}"] = {
                    "forecast": o["forecast"] * mult, "source": "Investing.com",
                    "ref": o.get("reference_period"), "captured": ev.get("fetchedAt")}
        self.cons = cons

    def obs(self, sid):
        if sid in self.fred:
            return self.fred[sid]["obs"]
        if sid in self.ecos:
            return self.ecos[sid]["obs"]
        if sid == "KR_EXPORT":
            return [[r["month"] + "-01", r["export_yoy_fraction"] * 100]
                    for r in self.exports.get("summary", []) if r.get("export_yoy_fraction") is not None]
        if sid == "NETLIQ":
            return self.netliq()
        raise KeyError(sid)

    def m(self, sid) -> dict:
        return {d: v for d, v in self.obs(sid)}

    def consensus(self, release, key):
        """발표일 기준 조회. 미국 발표일(UTC)과 수집 기준일(KST)이 하루 어긋날 수 있어 ±1일 허용."""
        c = self.cons.get(f"{release}|{key}")
        if c is None and release:
            d0 = dt.date.fromisoformat(release)
            for off in (-1, 1):
                c = self.cons.get(f"{(d0 + dt.timedelta(days=off)).isoformat()}|{key}")
                if c:
                    break
        return (c or {}).get("forecast"), (c or {}).get("source")

    def inv_event(self, key):
        for eid, ev in self.inv.get("events", {}).items():
            m = C.INVESTING_EVENTS.get(int(eid))
            if m and m[0] == key:
                return ev
        return None

    def netliq(self):
        tga = dict(self.obs("WTREGEN"))
        rrp = self.obs("RRPONTSYD")
        out = []
        for d, v in self.obs("WALCL"):
            r = next((x for dd, x in reversed(rrp) if dd <= d), 0.0)
            if d in tga:
                out.append([d, (v - tga[d] - r * 1000)])  # 백만$
        return out

    def asof(self, sid, date):
        """date 이하 가장 최근 관측치."""
        best = None
        for d, v in self.obs(sid):
            if d <= date:
                best = (d, v)
            else:
                break
        return best


# ─────────────────────────── 변환
def yoy_series(obs, freq):
    lag = {"M": 12, "Q": 4}.get(freq, 12)
    mp = dict(obs)
    out = []
    for d, v in obs:
        base = mp.get(add_months(d, -lag if freq == "M" else -3 * lag))
        out.append([d, (v / base - 1) * 100 if base else None])
    return out


def mom_series(obs):
    mp = dict(obs)
    return [[d, (v / mp[add_months(d, -1)] - 1) * 100 if add_months(d, -1) in mp else None] for d, v in obs]


def diff_series(obs):
    mp = dict(obs)
    return [[d, v - mp[add_months(d, -1)] if add_months(d, -1) in mp else None] for d, v in obs]


def ann(mp, d, n):
    b = mp.get(add_months(d, -n))
    return ((mp[d] / b) ** (12 / n) - 1) * 100 if b and d in mp else None


# ─────────────────────────── 보드
def kalshi_events(D):
    """회의일 순 정렬, 다음 2개 회의만."""
    def when(ev):
        try:
            return dt.datetime.strptime((ev.get("sub") or "").replace("On ", ""), "%b %d, %Y").date()
        except ValueError:
            return dt.date.max
    evs = sorted(D.kalshi, key=when)[:2]
    return [(when(ev), ev) for ev in evs]


def kalshi_dist(ev, cur_upper, floor=2.0):
    strikes = ev["strikes"]
    # 생존함수(상회확률)는 행사가에 대해 단조감소해야 함 → 비유동 호가 보정
    p_prev, cleaned = 1.0, []
    for s in strikes:
        p = min(s["pAbove"], p_prev)
        cleaned.append((s["strike"], p))
        p_prev = p
    probs = {}
    for i, (k, p) in enumerate(cleaned):
        nxt = cleaned[i + 1][1] if i + 1 < len(cleaned) else 0.0
        lvl = round(k + 0.25, 2)
        probs[lvl] = max(p - nxt, 0)
    if cleaned:
        probs[cleaned[0][0]] = probs.get(cleaned[0][0], 0) + (1 - cleaned[0][1])
    tot = sum(probs.values()) or 1
    # 비유동 행사가(스프레드 넓음)의 잡음 제거: floor% 미만 결과는 버리고 재정규화
    keep = {lvl: p for lvl, p in probs.items() if p / tot * 100 >= floor}
    tot = sum(keep.values()) or 1
    items = []
    for lvl, p in sorted(keep.items()):
        bp = round((lvl - cur_upper) * 100)
        lab = "동결" if bp == 0 else f"{bp:+d}bp"
        items.append({"label": lab, "level": lvl, "p": round(p / tot * 100, 1)})
    return items


def build_tile(D: Data, sid, tf, label, opt):
    scale = opt.get("scale", 1.0)
    if tf == "kalshi":
        evs = kalshi_events(D)
        if not evs:
            return None
        when, ev = evs[0]
        cur = D.obs("DFEDTARU")[-1][1]
        items = kalshi_dist(ev, cur)
        top = max(items, key=lambda x: x["p"])
        return {"label": label, "kind": "dist", "value": top["p"], "d": 0, "u": "%", "valueLabel": top["label"],
                "date": dt.date.today().isoformat(), "period": f"{when.month}/{when.day} 회의", "dist": items,
                "src": "Kalshi KXFED", "url": "https://kalshi.com/markets/kxfed",
                "note": "목표범위 상단 기준 시장 내재확률"}
    raw = D.obs(sid)
    freq = freq_of(raw)
    unit = opt.get("unit")
    if tf == "yoy":
        s, unit, d = yoy_series(raw, freq), "%", 1
    elif tf == "mom":
        s, unit, d = mom_series(raw), "%", 1
    elif tf == "diff":
        s, unit, d = diff_series(raw), "k", 0
    elif tf == "netliq":
        s, unit, d = [[x, v * 1e-6] for x, v in raw], "조$", 2
    else:
        s = [[x, v * scale] for x, v in raw]
        u0 = (D.fred.get(sid) or D.ecos.get(sid) or {}).get("unit", "")
        unit = unit or ("%" if u0 in ("%", "%p") else u0 if u0 in ("원", "$") else "")
        d = 2 if unit in ("%", "%p") and opt.get("chg") == "bp" else 1
        if sid in ("SP500", "KR_USDKRW", "HOUST"):
            d = 0 if sid != "KR_USDKRW" else 1
        if sid in ("KR_CA",):
            d = 0
        if sid in ("ICSA", "CCSA"):
            d = 0
        if sid in ("JTSJOL", "WALCL", "WRESBAL", "NFCI", "STLFSI4", "CFNAI"):
            d = 2
    valid = [x for x in s if x[1] is not None]
    if not valid:
        return None
    ld, lv = valid[-1]
    note = None
    if s[-1][1] is None:
        note = f"{ym(s[-1][0])} {'YoY' if tf == 'yoy' else '변화율'} 미공표 → {ym(ld)} 기준"
    subs = []

    def at_or_before(target):
        best = None
        for x, v in valid:
            if x <= target:
                best = v
        return best

    def chg(prev, lbl):
        if prev is None:
            return
        mode = opt.get("chg", "pp" if tf in ("yoy", "mom") else "abs")
        if tf in ("yoy", "mom"):
            mode = "pp"
        if mode == "bp":
            subs.append({"label": lbl, "v": (lv - prev) * 100, "d": 0, "u": "bp"})
        elif mode == "pct":
            subs.append({"label": lbl, "v": (lv / prev - 1) * 100 if prev else None, "d": 1, "u": "%"})
        elif mode == "pp":
            subs.append({"label": lbl, "v": lv - prev, "d": 1 if unit == "%" else 2, "u": "%p"})
        else:
            subs.append({"label": lbl, "v": lv - prev, "d": d, "u": unit if unit not in ("%",) else "%p"})

    if tf == "diff":
        last3 = [v for _, v in valid[-3:]]
        subs.append({"label": "3M 평균", "v": sum(last3) / len(last3), "d": 0, "u": "k"})
    elif freq == "D":
        dd = dt.date.fromisoformat(ld)
        chg(at_or_before((dd - dt.timedelta(days=7)).isoformat()), "1주")
        chg(at_or_before((dd - dt.timedelta(days=30)).isoformat()), "1개월")
    else:
        chg(valid[-2][1] if len(valid) > 1 else None,
            {"W": "전주", "M": "전월", "Q": "전분기", "A": "전년"}[freq])
    n = {"D": 130, "W": 52, "M": 24, "Q": 12, "A": 10}[freq]
    spark = [round(v, 4) if v is not None else None for _, v in s[-n:]]
    spark_d = [x for x, _ in s[-n:]]
    period = ld if freq in ("D", "W") else ym(ld) if freq == "M" else f"{ld[:4]}Q{(int(ld[5:7]) - 1) // 3 + 1}"
    src_info = D.fred.get(sid) and ("FRED " + sid, f"https://fred.stlouisfed.org/series/{sid}") or \
        (sid in D.ecos and ("ECOS " + D.ecos[sid].get("stat", ""), "https://ecos.bok.or.kr/")) or \
        (sid == "KR_EXPORT" and ("관세청·산업부 (수출 대시보드)", D.exports.get("sourceUrl") or "")) or \
        (sid == "NETLIQ" and ("FRED WALCL−WTREGEN−RRPONTSYD", "https://fred.stlouisfed.org/series/WALCL")) or ("", "")
    return {"label": label, "kind": "series", "value": lv, "d": d, "u": unit, "date": ld, "period": period,
            "freq": freq, "subs": subs, "spark": spark, "sparkD": spark_d, "note": note, "src": src_info[0], "url": src_info[1]}


def build_board(D: Data):
    groups = []
    for name, desc, items in C.BOARD:
        tiles = []
        for sid, tf, label, opt in items:
            try:
                t = build_tile(D, sid, tf, label, opt)
            except KeyError:
                t = None
            if t:
                tiles.append(t)
        groups.append({"name": name, "desc": desc, "tiles": tiles})
    return groups


# ─────────────────────────── 브리핑 공통
def reaction_table(D: Data, release: str, title="시장 반응 (발표일 종가 기준)"):
    rd = dt.date.fromisoformat(release)
    prev = (rd - dt.timedelta(days=1)).isoformat()
    kr_after = (rd + dt.timedelta(days=1)).isoformat()   # 미국 22:30 KST 발표 → 한국은 익일 반영
    rows = []
    spec = [("미 국채 2Y", "DGS2", "bp"), ("미 국채 10Y", "DGS10", "bp"), ("달러지수(Broad)", "DTWEXBGS", "%"),
            ("S&P 500", "SP500", "%"), ("VIX", "VIXCLS", "abs")]
    for lab, sid, mode in spec:
        a, b = D.asof(sid, prev), D.asof(sid, release)
        if not a or not b or b[0] != release:
            continue
        chg = (b[1] - a[1]) * 100 if mode == "bp" else (b[1] / a[1] - 1) * 100 if mode == "%" else b[1] - a[1]
        rows.append({"label": lab, "values": [a[1], b[1], chg], "fmt": [2, 2, 0 if mode == "bp" else 1],
                     "unit": "bp" if mode == "bp" else "%" if mode == "%" else ""})
    a, b = D.asof("KR_USDKRW", release), D.asof("KR_USDKRW", kr_after)
    if a and b and b[0] == kr_after:
        rows.append({"label": "원/달러 (익일 서울)", "values": [a[1], b[1], (b[1] / a[1] - 1) * 100],
                     "fmt": [1, 1, 2], "unit": "%"})
    if not rows:
        return None
    return {"type": "table", "title": title,
            "columns": [{"label": "직전 종가"}, {"label": f"{release[5:]} 종가"}, {"label": "변화", "heat": True, "s": True}],
            "rows": [{"label": r["label"], "values": r["values"], "fmt": r["fmt"], "unitLast": r["unit"]} for r in rows],
            "note": "미국 지표는 22:30 KST(서머타임 21:30) 발표 → 미국 자산은 발표일 종가, 원/달러는 익일 서울 종가와 비교."}


def chart_block(title, x, series, unit="", d=1, note=None, kind="line", zero=False):
    return {"type": "chart", "title": title, "x": x, "series": series, "unit": unit, "d": d,
            "note": note, "kind": kind, "zero": zero}


def months_back(end, n):
    return [add_months(end, -i) for i in range(n - 1, -1, -1)]


def surprise_block(D: Data, title, keys, unit, d, n=12, scale=1.0, note=None):
    """Investing.com 발표 이력으로 최근 n회 (실제 − 예측) 막대."""
    series, xs = [], None
    for key, name in keys:
        ev = D.inv_event(key)
        if not ev:
            continue
        occ = sorted([o for o in ev.get("occurrences", []) if o.get("actual") is not None and o.get("forecast") is not None],
                     key=lambda o: o["occurrence_time"])[-n:]
        mp = {o["occurrence_time"][:10]: (o["actual"] - o["forecast"]) * scale for o in occ}
        xs = sorted(set(xs or []) | set(mp))
        series.append((name, mp))
    if not series:
        return None
    xs = xs[-n:]
    return chart_block(title, xs, [{"name": nm, "kind": "bar", "values": [mp.get(x) for x in xs]} for nm, mp in series],
                       unit=unit, d=d, kind="bar", zero=True,
                       note=note or "Investing.com 발표 이력의 예측치 대비 실제치. x축 = 발표일.")


def build_upcoming(D: Data, days=21):
    now = dt.datetime.now(dt.timezone.utc)
    out = []
    for eid, ev in D.inv.get("events", {}).items():
        m = C.INVESTING_EVENTS.get(int(eid))
        nx = ev.get("next") if m else None
        if not nx:
            continue
        t = dt.datetime.fromisoformat(nx["occurrence_time"].replace("Z", "+00:00"))
        if not (now - dt.timedelta(hours=12) <= t <= now + dt.timedelta(days=days)):
            continue
        k = t.astimezone(KST)
        out.append({"kst": k.strftime("%Y-%m-%d %H:%M"), "dow": "월화수목금토일"[k.weekday()], "name": m[1],
                    "ref": nx.get("reference_period"), "forecast": nx.get("forecast"), "previous": nx.get("previous"),
                    "unit": ev.get("unit") or "", "d": ev.get("precision") if ev.get("precision") is not None else 1,
                    "dday": (k.date() - now.astimezone(KST).date()).days, "id": int(eid)})
    return {"updatedAt": D.inv.get("updatedAt"), "items": sorted(out, key=lambda x: x["kst"])}


# ─────────────────────────── 미국 CPI
CPI_ROWS = [
    ("CPIAUCSL", "전체 CPI", 0), ("CPIUFDSL", "식품", 1), ("CUSR0000SAF11", "가정식", 2), ("CUSR0000SEFV", "외식", 2),
    ("CPIENGSL", "에너지", 1), ("CUSR0000SETB01", "휘발유", 2), ("CPILFESL", "근원 CPI", 1),
    ("CUSR0000SACL1E", "근원 상품", 2), ("CUSR0000SETA01", "신차", 3), ("CUSR0000SETA02", "중고차", 3),
    ("CPIAPPSL", "의류", 3), ("CUSR0000SAM1", "의료 상품", 3), ("CUSR0000SASLE", "근원 서비스", 2),
    ("CUSR0000SAH1", "주거", 3), ("CUSR0000SEHA", "렌트", 4), ("CUSR0000SEHC", "OER(자가주거비)", 4),
    ("CUSR0000SEHB", "숙박", 4), ("SUPERCORE", "주거 제외 근원서비스(추정)", 3), ("CUSR0000SAM2", "의료 서비스", 3),
    ("CUSR0000SAS4", "운송 서비스", 3), ("CUSR0000SETG01", "항공료", 4),
]


def cpi_weights(D: Data, month: str):
    """2025.12 상대중요도를 (t-1)월까지의 상대가격 변화로 갱신."""
    base = C.CPI_WEIGHT_BASE
    allm = D.m("CPIAUCSL")
    prev = add_months(month, -1)
    w = {}
    for sid, w0 in C.CPI_WEIGHTS_DEC2025.items():
        mp = D.m(sid)
        if base in mp and prev in mp and base in allm and prev in allm:
            w[sid] = w0 * (mp[prev] / mp[base]) / (allm[prev] / allm[base])
    return w


def cpi_mom_with_super(D: Data, month: str):
    w = cpi_weights(D, month)
    moms = {}
    for sid, _, _ in CPI_ROWS:
        if sid == "SUPERCORE":
            continue
        mp = D.m(sid)
        p = add_months(month, -1)
        moms[sid] = (mp[month] / mp[p] - 1) * 100 if month in mp and p in mp else None
    cs, sh = moms.get("CUSR0000SASLE"), moms.get("CUSR0000SAH1")
    if cs is not None and sh is not None and "CUSR0000SASLE" in w and "CUSR0000SAH1" in w:
        wsc = w["CUSR0000SASLE"] - w["CUSR0000SAH1"]
        moms["SUPERCORE"] = (w["CUSR0000SASLE"] * cs - w["CUSR0000SAH1"] * sh) / wsc
        w["SUPERCORE"] = wsc
    else:
        moms["SUPERCORE"] = None
    return moms, w


def briefing_cpi(D: Data):
    meta = D.alfred.get("US_CPI") or {}
    month = meta.get("period") or D.obs("CPIAUCSL")[-1][0]
    release = meta.get("release") or ""
    hd, core = D.m("CPIAUCSL"), D.m("CPILFESL")
    moms, w = cpi_mom_with_super(D, month)
    prev_moms, _ = cpi_mom_with_super(D, add_months(month, -1))
    c_h, src = D.consensus(release, "US_CPI.headline_mom")
    c_c, _ = D.consensus(release, "US_CPI.core_mom")
    yoy_h = yoy_series(D.obs("CPIAUCSL"), "M")
    yoy_c = yoy_series(D.obs("CPILFESL"), "M")
    yh_now = dict(yoy_h).get(month)
    last_yoy = next((x for x in reversed(yoy_h) if x[1] is not None), None)
    last_cyoy = next((x for x in reversed(yoy_c) if x[1] is not None), None)
    a3c, a6c, a3h = ann(core, month, 3), ann(core, month, 6), ann(hd, month, 3)

    cards = [
        {"label": "헤드라인 CPI (MoM)", "value": moms["CPIAUCSL"], "d": 1, "u": "%", "s": True, "raw": moms["CPIAUCSL"],
         "cons": c_h, "prior": prev_moms["CPIAUCSL"]},
        {"label": "근원 CPI (MoM)", "value": moms["CPILFESL"], "d": 1, "u": "%", "s": True, "raw": moms["CPILFESL"],
         "cons": c_c, "prior": prev_moms["CPILFESL"]},
        {"label": "헤드라인 CPI (YoY)", "value": yh_now, "d": 1, "u": "%",
         "missing": "미공표" if yh_now is None else None,
         "foot": f"직전 공표 {ym(last_yoy[0])} {last_yoy[1]:.1f}% · 3M 연율 {a3h:.1f}%" if last_yoy and a3h is not None else None},
        {"label": "근원 CPI (3M·6M 연율)", "value": a3c, "d": 1, "u": "%",
         "foot": f"6M 연율 {a6c:.1f}% · 직전 공표 YoY {ym(last_cyoy[0])} {last_cyoy[1]:.1f}%" if a6c is not None and last_cyoy else None},
    ]
    for c in cards:
        if c.get("cons") is not None and c.get("value") is not None:
            c["surprise"] = round(c["value"], 1) - c["cons"]
    # 요약표 (MoM 6개월 + 가중치)
    cols6 = months_back(month, 6)
    table_rows = []
    for sid, lab, depth in CPI_ROWS:
        vals = []
        for mth in cols6:
            mm, _ = cpi_mom_with_super(D, mth) if sid == "SUPERCORE" else (None, None)
            if sid == "SUPERCORE":
                vals.append(mm.get("SUPERCORE"))
            else:
                mp = D.m(sid)
                p = add_months(mth, -1)
                vals.append((mp[mth] / mp[p] - 1) * 100 if mth in mp and p in mp else None)
        a3 = None
        if sid != "SUPERCORE":
            a3 = ann(D.m(sid), month, 3)
        table_rows.append({"label": lab, "depth": depth, "values": [w.get(sid)] + vals + [a3],
                           "emph": depth <= 1, "est": sid == "SUPERCORE"})
    table = {"type": "table", "title": "품목별 전월비(SA, %) — 최근 6개월",
             "columns": [{"label": "가중치", "d": 1}] + [{"label": ym(x)[2:], "d": 1, "s": True, "heat": True,
                                                          "hl": x == month} for x in cols6] +
                        [{"label": "3M 연율", "d": 1, "s": True}],
             "rows": table_rows,
             "note": "가중치 = BLS 2025.12 상대중요도를 전월까지의 상대가격 변화로 갱신한 추정치. "
                     "주거 제외 근원서비스 = (근원서비스×가중치 − 주거×가중치)/잔여 가중치로 역산한 추정치. "
                     "2025.10 미조사로 2025.11 전월비는 공란."}
    # 기여도
    parts = [("CPIUFDSL", "식품"), ("CPIENGSL", "에너지"), ("CUSR0000SACL1E", "근원 상품"),
             ("CUSR0000SAH1", "주거"), ("SUPERCORE", "주거 제외 근원서비스")]
    contrib = [{"label": lab, "value": w[sid] * moms[sid] / 100} for sid, lab in parts
               if moms.get(sid) is not None and sid in w]
    contrib_total = sum(x["value"] for x in contrib)
    leaf = [("CUSR0000SAF11", "가정식"), ("CUSR0000SEFV", "외식"), ("CUSR0000SETB01", "휘발유"),
            ("CUSR0000SETA01", "신차"), ("CUSR0000SETA02", "중고차"), ("CPIAPPSL", "의류"),
            ("CUSR0000SAM1", "의료 상품"), ("CUSR0000SEHA", "렌트"), ("CUSR0000SEHC", "OER"),
            ("CUSR0000SEHB", "숙박"), ("CUSR0000SAM2", "의료 서비스"), ("CUSR0000SAS4", "운송 서비스")]
    leaf_c = sorted([{"label": lab, "value": w[sid] * moms[sid] / 100} for sid, lab in leaf
                     if moms.get(sid) is not None and sid in w], key=lambda x: -abs(x["value"]))
    # 차트
    x24 = months_back(month, 24)
    mh, mc = dict(mom_series(D.obs("CPIAUCSL"))), dict(mom_series(D.obs("CPILFESL")))
    ch1 = chart_block("헤드라인·근원 CPI 전월비", x24, [
        {"name": "근원", "kind": "bar", "values": [mc.get(x) for x in x24]},
        {"name": "헤드라인", "kind": "line", "values": [mh.get(x) for x in x24]}],
        unit="%", d=1, kind="combo", zero=True, note="공란 = 2025.10 미조사 영향(2025.10·11)")
    a3s = {sid: {x: ann(D.m(sid), x, 3) for x in x24} for sid in ("CUSR0000SACL1E", "CUSR0000SASLE", "CUSR0000SAH1")}
    ch2 = chart_block("근원 상품·서비스·주거 3개월 연율", x24, [
        {"name": "근원 상품", "values": [a3s["CUSR0000SACL1E"][x] for x in x24]},
        {"name": "근원 서비스", "values": [a3s["CUSR0000SASLE"][x] for x in x24]},
        {"name": "주거", "values": [a3s["CUSR0000SAH1"][x] for x in x24]}], unit="%", d=1, zero=True,
        note="공란 = 2025.10 지수 부재로 3개월 연율 계산 불가 구간.")
    x36 = months_back(month, 36)
    sticky = D.m("CORESTICKM159SFRBATL")
    ch3 = chart_block("전년비 추이 — 10월은 공표 없음", x36, [
        {"name": "헤드라인 YoY", "values": [dict(yoy_h).get(x) for x in x36]},
        {"name": "근원 YoY", "values": [dict(yoy_c).get(x) for x in x36]},
        {"name": "경직 근원(애틀랜타 연은)", "values": [sticky.get(x) for x in x36]}], unit="%", d=1)
    # 요약 3줄
    top_up = [x for x in leaf_c if x["value"] > 0][:2]
    top_dn = [x for x in leaf_c if x["value"] < 0][:1]
    s1 = (f"헤드라인 CPI {sgn(moms['CPIAUCSL'])}% MoM으로 예상({sgn(c_h)}%) {word(moms['CPIAUCSL'], c_h)}, "
          f"근원 {sgn(moms['CPILFESL'])}%(예상 {sgn(c_c)}%) {word(moms['CPILFESL'], c_c)}") if c_h is not None else \
        f"헤드라인 CPI {sgn(moms['CPIAUCSL'])}% MoM, 근원 {sgn(moms['CPILFESL'])}%"
    s2 = ("기여: " + ", ".join(f"{x['label']} {x['value']:+.2f}%p" for x in sorted(contrib, key=lambda x: -x['value'])[:3]) +
          (" / 세부 상승 " + "·".join(x["label"] for x in top_up) if top_up else "") +
          (", 하락 " + "·".join(x["label"] for x in top_dn) if top_dn else ""))
    s3 = (f"2025.10 미조사로 전년비 미공표 → 근원 3M 연율 {a3c:.1f}%·6M {a6c:.1f}%로 추세 판단"
          if yh_now is None else f"헤드라인 YoY {yh_now:.1f}%, 근원 3M 연율 {a3c:.1f}%")
    blocks = [
        {"type": "cards", "items": cards, "consSource": src},
        {"type": "bars", "title": "헤드라인 전월비 기여도 (%p)", "items": contrib, "d": 2, "unit": "%p",
         "total": {"label": "합계(추정)", "value": contrib_total, "actual": moms["CPIAUCSL"]},
         "note": "기여도 = 갱신 가중치 × 품목 전월비. 합계와 실제 헤드라인 차이는 반올림·가중치 근사 오차."},
        {"type": "bars", "title": "세부 품목 기여 상위 (%p)", "items": leaf_c[:8], "d": 3, "unit": "%p"},
        table, ch1, ch2, ch3,
    ]
    sb = surprise_block(D, "컨센서스 대비 서프라이즈 (MoM, %p) — 최근 12회",
                        [("US_CPI.headline_mom", "헤드라인"), ("US_CPI.core_mom", "근원")], "%p", 1)
    if sb:
        blocks.append(sb)
    rt = reaction_table(D, release) if release else None
    if rt:
        blocks.append(rt)
    return {"id": "us-cpi", "title": "미국 소비자물가(CPI)", "region": "US", "tier": 1, "period": month,
            "periodLabel": ko_month(month), "release": release, "releaseTime": "22:30 KST",
            "summary": [s1, s2, s3], "blocks": blocks,
            "sources": [{"name": "BLS CPI", "url": "https://www.bls.gov/cpi/"},
                        {"name": "FRED", "url": "https://fred.stlouisfed.org/"},
                        {"name": "BLS 상대중요도(2025.12)", "url": "https://www.bls.gov/cpi/tables/relative-importance/2025.htm"}]}


# ─────────────────────────── 미국 고용
NFP_ROWS = [("PAYEMS", "비농업 전체", 0), ("USPRIV", "민간", 1), ("USMINE", "광업·벌목", 2), ("USCONS", "건설", 2),
            ("MANEMP", "제조", 2), ("USTPU", "무역·운송·유틸", 2), ("USTRADE", "소매", 3), ("USINFO", "정보", 2),
            ("USFIRE", "금융", 2), ("USPBS", "전문·사업서비스", 2), ("TEMPHELPS", "임시직", 3), ("USEHS", "교육·헬스", 2),
            ("CES6562000001", "헬스케어", 3), ("USLAH", "레저·접객", 2), ("USSERV", "기타 서비스", 2), ("USGOVT", "정부", 1)]


def briefing_nfp(D: Data):
    meta = D.alfred.get("US_NFP") or {}
    month = meta.get("period") or D.obs("PAYEMS")[-1][0]
    release = meta.get("release") or ""
    pay = D.m("PAYEMS")
    chg = lambda mp, mth: (mp[mth] - mp[add_months(mth, -1)]) if mth in mp and add_months(mth, -1) in mp else None
    nfp, nfp_prev = chg(pay, month), chg(pay, add_months(month, -1))
    prevv = dict(meta.get("prevObs") or [])
    p1, p2 = add_months(month, -1), add_months(month, -2)
    rev = None
    rev_rows = []
    if prevv and p1 in prevv and add_months(month, -3) in prevv:
        rev = (pay[p1] - pay[add_months(month, -3)]) - (prevv[p1] - prevv[add_months(month, -3)])
        for mth in (p2, p1):
            before = prevv[mth] - prevv.get(add_months(mth, -1), float("nan"))
            after = chg(pay, mth)
            rev_rows.append({"label": ko_month(mth), "values": [before, after, after - before], "fmt": [0, 0, 0]})
    ur, ahe = D.m("UNRATE"), D.m("CES0500000003")
    ahe_mom = (ahe[month] / ahe[p1] - 1) * 100
    ahe_yoy = (ahe[month] / ahe[add_months(month, -12)] - 1) * 100
    c_n, src = D.consensus(release, "US_NFP.nfp")
    c_u, _ = D.consensus(release, "US_NFP.ur")
    c_a, _ = D.consensus(release, "US_NFP.ahe_mom")
    cards = [
        {"label": "비농업 고용 증감", "value": nfp, "d": 0, "u": "k", "s": True,
         "cons": c_n / 1000 if c_n else None, "prior": nfp_prev, "priorNote": "수정 후"},
        {"label": "실업률", "value": ur[month], "d": 1, "u": "%", "cons": c_u, "prior": ur[p1]},
        {"label": "평균시급 (MoM)", "value": ahe_mom, "d": 1, "u": "%", "s": True, "cons": c_a,
         "prior": (ahe[p1] / ahe[p2] - 1) * 100, "foot": f"YoY {ahe_yoy:.1f}%"},
        {"label": "직전 2개월 수정", "value": rev, "d": 0, "u": "k", "s": True,
         "foot": f"{ym(p2)}·{ym(p1)} 합산 (ALFRED 빈티지 비교)"},
    ]
    for c in cards:
        if c.get("cons") is not None and c.get("value") is not None:
            c["surprise"] = round(c["value"], c["d"]) - c["cons"]
    cols6 = months_back(month, 6)
    rows = []
    sec_bars = []
    for sid, lab, depth in NFP_ROWS:
        mp = D.m(sid)
        vals = [chg(mp, x) for x in cols6]
        avg3 = sum(v for v in vals[-3:] if v is not None) / 3
        rows.append({"label": lab, "depth": depth, "values": vals + [avg3], "emph": depth == 0})
        if depth == 2 or sid == "USGOVT":
            sec_bars.append({"label": lab, "value": chg(mp, month)})
    table = {"type": "table", "title": "업종별 고용 증감 (천명, SA) — 최근 6개월",
             "columns": [{"label": ym(x)[2:], "d": 0, "s": True, "heat": True, "hl": x == month} for x in cols6] +
                        [{"label": "3M 평균", "d": 0, "s": True}], "rows": rows,
             "note": "최근 2개월은 다음 발표에서 다시 수정된다(예비치)."}
    hh = [("UNRATE", "실업률", "%", 1), ("U6RATE", "U-6 실업률", "%", 1), ("CIVPART", "경제활동참가율", "%", 1),
          ("LNS11300060", "핵심연령(25-54) 참가율", "%", 1), ("LNS12300060", "핵심연령 고용률", "%", 1),
          ("LNS12032194", "비자발적 시간제(천명)", "", 0), ("UEMP27OV", "27주+ 장기실업(천명)", "", 0),
          ("AWHAETP", "주당 근로시간", "", 1)]
    hrows = []
    for sid, lab, u, d in hh:
        mp = D.m(sid)
        hrows.append({"label": lab, "values": [mp.get(x) for x in cols6] +
                      [(mp[month] - mp[p1]) if month in mp and p1 in mp else None], "fmt": [d] * 6 + [d]})
    aherow = {"label": "평균시급 YoY", "values": [(ahe[x] / ahe[add_months(x, -12)] - 1) * 100 for x in cols6] +
              [ahe_yoy - (ahe[p1] / ahe[add_months(p1, -12)] - 1) * 100], "fmt": [1] * 7}
    htable = {"type": "table", "title": "가계조사·임금 — 최근 6개월",
              "columns": [{"label": ym(x)[2:], "hl": x == month} for x in cols6] + [{"label": "전월 대비", "s": True, "heat": True}],
              "rows": hrows + [aherow]}
    x24 = months_back(month, 24)
    pchg = {x: chg(pay, x) for x in x24}
    avg3 = {}
    for i, x in enumerate(x24):
        win = [pchg[y] for y in x24[max(0, i - 2):i + 1] if pchg[y] is not None]
        avg3[x] = sum(win) / len(win) if len(win) == 3 else None
    ch1 = chart_block("비농업 고용 증감 (천명)", x24, [
        {"name": "월간 증감", "kind": "bar", "values": [pchg[x] for x in x24]},
        {"name": "3개월 평균", "kind": "line", "values": [avg3[x] for x in x24]}], unit="k", d=0, kind="combo", zero=True)
    x36 = months_back(month, 36)
    u6 = D.m("U6RATE")
    ch2 = chart_block("실업률 (%)", x36, [{"name": "실업률(U-3)", "values": [ur.get(x) for x in x36]},
                                           {"name": "U-6", "values": [u6.get(x) for x in x36]}], unit="%", d=1)
    cpiy = dict(yoy_series(D.obs("CPIAUCSL"), "M"))
    ch3 = chart_block("평균시급 vs CPI 전년비 (%)", x36, [
        {"name": "평균시급 YoY", "values": [(ahe[x] / ahe[add_months(x, -12)] - 1) * 100 if add_months(x, -12) in ahe else None for x in x36]},
        {"name": "CPI YoY", "values": [cpiy.get(x) for x in x36]}], unit="%", d=1,
        note="실질임금 = 두 선의 차이. CPI 2025.10·2026.10 YoY는 공표 없음.")
    sec_sorted = sorted([x for x in sec_bars if x["value"] is not None], key=lambda x: -x["value"])
    s1 = (f"비농업 고용 {sgn(nfp, 0)}k로 예상({sgn(c_n / 1000, 0)}k) {word(nfp, c_n / 1000, 0)}, 실업률 {ur[month]:.1f}%"
          f"(예상 {c_u:.1f}%)") if c_n else f"비농업 고용 {sgn(nfp, 0)}k, 실업률 {ur[month]:.1f}%"
    s2 = (f"증가 주도: " + ", ".join(f"{x['label']} {x['value']:+.0f}k" for x in sec_sorted[:3]) +
          (f" / 감소: {sec_sorted[-1]['label']} {sec_sorted[-1]['value']:+.0f}k" if sec_sorted and sec_sorted[-1]['value'] < 0 else ""))
    s3 = (f"직전 2개월 {sgn(rev, 0)}k 수정, 평균시급 {sgn(ahe_mom)}% MoM·{ahe_yoy:.1f}% YoY, "
          f"U-6 {u6[month]:.1f}%({sgn(u6[month] - u6[p1])}%p)") if rev is not None else f"평균시급 {ahe_yoy:.1f}% YoY"
    blocks = [{"type": "cards", "items": cards, "consSource": src},
              {"type": "bars", "title": f"{ko_month(month)} 업종별 고용 증감 (천명)", "items": sec_sorted, "d": 0, "unit": "k"}]
    if rev_rows:
        blocks.append({"type": "table", "title": "직전 월 수정 (천명)",
                       "columns": [{"label": "수정 전", "s": True}, {"label": "수정 후", "s": True}, {"label": "수정폭", "s": True, "heat": True}],
                       "rows": rev_rows, "note": f"수정 전 = 발표 전일({meta.get('prevVintage')}) ALFRED 빈티지."})
    blocks += [table, htable, ch1, ch2, ch3]
    sb = surprise_block(D, "비농업 고용 서프라이즈 (천명) — 최근 12회", [("US_NFP.nfp", "실제 − 예측")], "k", 0)
    if sb:
        blocks.append(sb)
    rt = reaction_table(D, release) if release else None
    if rt:
        blocks.append(rt)
    return {"id": "us-nfp", "title": "미국 고용보고서", "region": "US", "tier": 1, "period": month,
            "periodLabel": ko_month(month), "release": release, "releaseTime": "22:30 KST",
            "summary": [s1, s2, s3], "blocks": blocks,
            "sources": [{"name": "BLS Employment Situation", "url": "https://www.bls.gov/news.release/empsit.toc.htm"},
                        {"name": "ALFRED (빈티지)", "url": "https://alfred.stlouisfed.org/series?seid=PAYEMS"}]}


# ─────────────────────────── FOMC
def word_diff(a: str, b: str):
    ta, tb = a.split(" "), b.split(" ")
    sm = difflib.SequenceMatcher(a=ta, b=tb, autojunk=False)
    segs = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            segs.append(["=", " ".join(ta[i1:i2])])
        else:
            if i2 > i1:
                segs.append(["-", " ".join(ta[i1:i2])])
            if j2 > j1:
                segs.append(["+", " ".join(tb[j1:j2])])
    return segs


def briefing_fomc(D: Data):
    if len(D.fomc) < 2:
        return None
    prev, cur = D.fomc[-2], D.fomc[-1]
    date = cur["date"]
    lo_b, hi_b = D.asof("DFEDTARL", (dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat()), \
        D.asof("DFEDTARU", (dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat())
    lo_a, hi_a = D.asof("DFEDTARL", (dt.date.fromisoformat(date) + dt.timedelta(days=1)).isoformat()), \
        D.asof("DFEDTARU", (dt.date.fromisoformat(date) + dt.timedelta(days=1)).isoformat())
    move = round((hi_a[1] - hi_b[1]) * 100)
    decision = "동결" if move == 0 else f"{abs(move)}bp {'인상' if move > 0 else '인하'}"
    vote = cur.get("vote")
    dissent = cur.get("dissent") or ""
    names = re.findall(r"([A-Z][a-z]+(?: [A-Z]\.)? [A-Z][a-z]+)", dissent.split("who")[0]) if dissent else []
    cur_upper = D.obs("DFEDTARU")[-1][1]
    dists = [{"label": f"{w.year}.{w.month}.{w.day} 회의", "items": kalshi_dist(ev, cur_upper)} for w, ev in kalshi_events(D)]
    nxt = dists[0]["items"] if dists else []
    top = max(nxt, key=lambda x: x["p"]) if nxt else None
    sep = {sid: {d[:4]: v for d, v in D.obs(sid)} for sid in ("FEDTARMD", "UNRATEMD", "PCECTPIMD", "JCXFEMD", "GDPC1MD")}
    yrs = sorted(sep["FEDTARMD"].keys())
    lr = D.obs("FEDTARMDLR")[-1]
    sep_rows = [{"label": lab, "values": [sep[sid].get(y) for y in yrs] + ([lr[1]] if sid == "FEDTARMD" else [None])}
                for sid, lab in (("FEDTARMD", "정책금리(중간값)"), ("UNRATEMD", "실업률(4Q)"), ("PCECTPIMD", "PCE 물가(4Q YoY)"),
                                 ("JCXFEMD", "근원 PCE(4Q YoY)"), ("GDPC1MD", "실질 GDP(4Q YoY)"))]
    c_rate, _ = D.consensus(date, "US_FOMC.rate")
    cards = [
        {"label": "정책금리 결정", "valueText": decision,
         "foot": f"목표범위 {lo_a[1]:.2f}–{hi_a[1]:.2f}%" + (f" · 예상(상단) {c_rate:.2f}% {'부합' if abs(c_rate - hi_a[1]) < 1e-6 else '불일치'}" if c_rate is not None else "")},
        {"label": "표결", "valueText": f"{vote[0]}–{vote[1]}" if vote else "–",
         "foot": ("반대: " + ", ".join(names) + (" (인상 선호)" if "raise" in dissent else " (인하 선호)" if "lower" in dissent else ""))
         if names else "만장일치"},
        {"label": "다음 회의 시장 확률", "valueText": f"{top['label']} {top['p']:.0f}%" if top else "–",
         "foot": f"Kalshi · {dists[0]['label']}" if dists else ""},
        {"label": "SEP 장기 정책금리", "value": lr[1], "d": 2, "u": "%", "foot": f"{lr[0]} SEP 기준"},
    ]
    seg = word_diff(" ¶ ".join(prev["paragraphs"]), " ¶ ".join(cur["paragraphs"]))
    # 금리 경로 차트: 2Y vs 목표 상단 vs EFFR
    xs = [d for d, _ in D.obs("DGS2") if d >= "2025-06-01"]
    up, ef, g2 = D.m("DFEDTARU"), D.m("EFFR"), D.m("DGS2")
    def ff(mp):
        out, last = [], None
        for x in xs:
            last = mp.get(x, last)
            out.append(last)
        return out
    ch = chart_block("정책금리와 2년물", xs, [{"name": "목표 상단", "values": ff(up)}, {"name": "실효 FFR", "values": ff(ef)},
                                          {"name": "미 국채 2Y", "values": ff(g2)}], unit="%", d=2)
    changes = sum(1 for s in seg if s[0] != "=")
    s1 = f"{int(date[5:7])}월 FOMC {decision}(목표범위 {lo_a[1]:.2f}–{hi_a[1]:.2f}%), 표결 {vote[0]}–{vote[1]}" + \
        (f" — {', '.join(names)} 반대" if names else "") if vote else f"FOMC {decision}"
    s2 = f"성명서: 직전({prev['date']}) 대비 문구 변경 {changes}곳 — 경기 판단·정책 문구 변화는 아래 비교 참고"
    s3 = (f"시장: {dists[0]['label']} 회의 {top['label']} 확률 {top['p']:.0f}% (Kalshi), "
          f"2Y {g2[max(k for k in g2 if k <= dt.date.today().isoformat())]:.2f}%") if top else ""
    blocks = [{"type": "cards", "items": cards},
              {"type": "diff", "title": f"성명서 비교 ({prev['date']} → {date})", "segments": seg,
               "note": "빨간 취소선 = 삭제, 초록 = 추가. 투표 문단 제외."},
              {"type": "dist", "title": "향후 회의 정책금리(목표 상단) 시장 확률 — Kalshi", "groups": dists,
               "note": "Kalshi 'Above X%' 계약 가격을 구간 확률로 변환(호가 단조성 보정, 2% 미만 결과 제외 후 재정규화). 연준 공식 전망 아님."},
              {"type": "table", "wide": False, "title": f"SEP 중간값 ({D.obs('FEDTARMDLR')[-1][0]} 발표)",
               "columns": [{"label": y, "d": 1} for y in yrs] + [{"label": "장기", "d": 1}], "rows": sep_rows},
              ch]
    rt = reaction_table(D, date, "시장 반응 (결정일 종가 기준)")
    if rt:
        rt["note"] = "성명 04:00 KST(서머타임 03:00) 발표 → 미국 자산은 결정일 종가, 원/달러는 익일 서울 종가."
        blocks.append(rt)
    return {"id": "us-fomc", "title": "FOMC 통화정책 결정", "region": "US", "tier": 1, "period": date,
            "periodLabel": f"{date[:4]}년 {int(date[5:7])}월 {int(date[8:])}일", "release": date, "releaseTime": "03:00 KST",
            "summary": [s1, s2, s3], "blocks": blocks,
            "sources": [{"name": "FOMC 성명", "url": cur["url"]}, {"name": "직전 성명", "url": prev["url"]},
                        {"name": "Kalshi KXFED", "url": "https://kalshi.com/markets/kxfed"}]}


# ─────────────────────────── 한국 수출
def briefing_exports(D: Data):
    ex = D.exports
    if not ex:
        return None
    summ = {r["month"]: r for r in ex["summary"]}
    month = ex["asOf"]
    cur = summ[month]
    ly = summ.get(f"{int(month[:4]) - 1}{month[4:]}")
    inds = {}
    for r in ex["industries"]:
        inds.setdefault(r["industry"], {})[r["month"]] = r
    lym = f"{int(month[:4]) - 1}{month[4:]}"
    rows, contrib = [], []
    for name, mp in inds.items():
        if month not in mp:
            continue
        v = mp[month]["export_usd"]
        base = mp.get(lym, {}).get("export_usd")
        c = (v - base) / ly["export_usd"] * 100 if base and ly else None
        rows.append({"label": name, "values": [v / 1e8, (mp[month].get("yoy_fraction") or 0) * 100, c, v / cur["export_usd"] * 100]})
        if c is not None:
            contrib.append({"label": name, "value": c})
    rows.sort(key=lambda r: -(r["values"][0] or 0))
    contrib.sort(key=lambda x: -x["value"])
    semi = inds.get("반도체", {})
    semi_c = next((x["value"] for x in contrib if x["label"] == "반도체"), None)
    yoy = cur["export_yoy_fraction"] * 100
    ex_semi_yoy = None
    if "반도체" in inds and month in semi and lym in semi and ly:
        ex_semi_yoy = ((cur["export_usd"] - semi[month]["export_usd"]) / (ly["export_usd"] - semi[lym]["export_usd"]) - 1) * 100
    prev_m = add_months(month + "-01", -1)[:7]
    cards = [
        {"label": "수출액", "value": cur["export_usd"] / 1e8, "d": 0, "u": "억$",
         "foot": f"YoY {yoy:+.1f}% · 전월 {summ[prev_m]['export_yoy_fraction'] * 100:+.1f}%"},
        {"label": "일평균 수출", "value": cur["avg_daily_export_usd"] / 1e8, "d": 1, "u": "억$",
         "foot": f"YoY {(cur['avg_daily_export_usd'] / ly['avg_daily_export_usd'] - 1) * 100:+.1f}%" if ly else None},
        {"label": "반도체 제외 수출 YoY", "value": ex_semi_yoy, "d": 1, "u": "%", "s": True,
         "foot": f"반도체 기여 {semi_c:+.1f}%p / 전체 {yoy:+.1f}%" if semi_c is not None else None},
        {"label": "무역수지", "value": cur["trade_balance_usd"] / 1e8, "d": 0, "u": "억$", "s": True,
         "foot": f"수입 {cur['import_usd'] / 1e8:,.0f}억$ (YoY {(cur['import_usd'] / ly['import_usd'] - 1) * 100:+.1f}%)" if ly else None},
    ]
    table = {"type": "table", "title": f"20대 품목 수출 ({ko_month(month + '-01')})",
             "columns": [{"label": "수출액(억$)", "d": 1}, {"label": "YoY(%)", "d": 1, "s": True, "heat": True},
                         {"label": "기여도(%p)", "d": 1, "s": True, "heat": True}, {"label": "비중(%)", "d": 1}],
             "rows": rows, "note": "기여도 = (품목 수출 − 전년동월 품목 수출) / 전년동월 총수출."}
    x24 = [add_months(month + "-01", -i)[:7] for i in range(23, -1, -1)]
    tot = [summ[x]["export_usd"] / 1e8 if x in summ else None for x in x24]
    sem = [semi[x]["export_usd"] / 1e8 if x in semi else None for x in x24]
    ch1 = chart_block("월별 수출액 (억$)", [x + "-01" for x in x24], [
        {"name": "총수출", "kind": "bar", "values": tot}, {"name": "반도체", "kind": "line", "values": sem}],
        unit="억$", d=0, kind="combo", zero=True)
    def yoy_of(mp, x, key="export_usd"):
        b = mp.get(f"{int(x[:4]) - 1}{x[4:]}")
        return (mp[x][key] / b[key] - 1) * 100 if x in mp and b else None
    exs = {}
    for x in x24:
        if x in summ and x in semi:
            exs[x] = {"export_usd": summ[x]["export_usd"] - semi[x]["export_usd"]}
    ly_keys = [f"{int(x[:4]) - 1}{x[4:]}" for x in x24]
    for x in ly_keys:
        if x in summ and x in semi:
            exs[x] = {"export_usd": summ[x]["export_usd"] - semi[x]["export_usd"]}
    ch2 = chart_block("수출 전년비 (%)", [x + "-01" for x in x24], [
        {"name": "총수출", "values": [yoy_of(summ, x) for x in x24]},
        {"name": "반도체 제외", "values": [yoy_of(exs, x) for x in x24]}], unit="%", d=1, zero=True,
        note="반도체 품목 시계열은 2022년부터 제공 → 반도체 제외 YoY는 2023년 이후 계산 가능.")
    s1 = f"{ko_month(month + '-01')} 수출 {cur['export_usd'] / 1e8:,.0f}억$ ({yoy:+.1f}% YoY), 일평균 {cur['avg_daily_export_usd'] / 1e8:.1f}억$"
    s2 = "기여 상위: " + ", ".join(f"{x['label']} {x['value']:+.1f}%p" for x in contrib[:3]) + \
         (f" / 반도체 제외 {ex_semi_yoy:+.1f}%" if ex_semi_yoy is not None else "")
    s3 = f"무역수지 {cur['trade_balance_usd'] / 1e8:+,.0f}억$, 수입 {(cur['import_usd'] / ly['import_usd'] - 1) * 100:+.1f}% YoY" if ly else ""
    return {"id": "kr-exports", "title": "한국 수출입동향", "region": "KR", "tier": 1, "period": month + "-01",
            "periodLabel": ko_month(month + "-01"), "release": (ex.get("updatedAt") or "")[:10], "releaseTime": "",
            "releaseNote": "확정치 갱신",
            "summary": [s1, s2, s3],
            "blocks": [{"type": "cards", "items": cards},
                       {"type": "bars", "title": "총수출 YoY 품목별 기여도 (%p) — 상위 6·하위 4", "items": contrib[:6] + contrib[-4:], "d": 1, "unit": "%p",
                        "total": {"label": "총수출 YoY", "value": yoy}},
                       table, ch1, ch2,
                       {"type": "note", "items": [f"수출 대시보드 확정치({ex.get('status')}) 기준 {ex['asOf']}. "
                                                  "10월 잠정치(11/1 산업부 발표)는 수출 파이프라인 갱신 후 자동 반영."]}],
            "sources": [{"name": "관세청·산업통상부 수출입동향", "url": ex.get("sourceUrl") or "https://www.motir.go.kr/"}]}


# ─────────────────────────── 경량 카드 (Tier 3)
def briefing_retail(D: Data):
    meta = D.alfred.get("US_RETAIL") or {}
    month, release = meta.get("period"), meta.get("release") or ""
    if not month:
        return None
    m = {k: D.m(k) for k in ("RSAFS", "RSFSXMV", "RSXFS", "RSGASS", "RSBMGESD", "RSFSDP", "RSMVPD", "RSNSR")}
    # 컨트롤그룹 = 소매업(음식점 제외) − 자동차·부품 − 주유소 − 건자재
    ctrl = {x: m["RSXFS"][x] - m["RSMVPD"][x] - m["RSGASS"][x] - m["RSBMGESD"][x] for x in m["RSXFS"]
            if x in m["RSMVPD"] and x in m["RSGASS"] and x in m["RSBMGESD"]}
    pc = lambda mp, x: (mp[x] / mp[add_months(x, -1)] - 1) * 100 if x in mp and add_months(x, -1) in mp else None
    c_h, src = D.consensus(release, "US_RETAIL.headline_mom")
    c_x, _ = D.consensus(release, "US_RETAIL.exauto_mom")
    prevv = dict(meta.get("prevObs") or [])
    p1 = add_months(month, -1)
    prior_before = (prevv[p1] / prevv[add_months(month, -2)] - 1) * 100 if p1 in prevv and add_months(month, -2) in prevv else None
    cards = [{"label": "소매판매 (MoM)", "value": pc(m["RSAFS"], month), "d": 1, "u": "%", "s": True, "cons": c_h,
              "prior": pc(m["RSAFS"], p1), "priorNote": f"수정 전 {prior_before:+.1f}%" if prior_before is not None else None},
             {"label": "자동차 제외 (MoM)", "value": pc(m["RSFSXMV"], month), "d": 1, "u": "%", "s": True, "cons": c_x,
              "prior": pc(m["RSFSXMV"], p1)},
             {"label": "컨트롤그룹 (MoM)", "value": pc(ctrl, month), "d": 1, "u": "%", "s": True, "prior": pc(ctrl, p1),
              "foot": "자동차·주유소·건자재·음식점 제외 (GDP 소비 반영)"}]
    for c in cards:
        if c.get("cons") is not None:
            c["surprise"] = round(c["value"], 1) - c["cons"]
    cols = months_back(month, 6)
    rows = [{"label": lab, "values": [pc(mp, x) for x in cols]} for lab, mp in
            (("전체", m["RSAFS"]), ("자동차 제외", m["RSFSXMV"]), ("컨트롤그룹", ctrl), ("자동차·부품", m["RSMVPD"]),
             ("주유소", m["RSGASS"]), ("건자재", m["RSBMGESD"]), ("음식점", m["RSFSDP"]), ("무점포(온라인)", m["RSNSR"]))]
    x24 = months_back(month, 24)
    ch = chart_block("소매판매 전월비 (%)", x24, [{"name": "컨트롤그룹", "kind": "bar", "values": [pc(ctrl, x) for x in x24]},
                                                {"name": "전체", "kind": "line", "values": [pc(m["RSAFS"], x) for x in x24]}],
                     unit="%", d=1, kind="combo", zero=True)
    s1 = f"소매판매 {sgn(cards[0]['value'])}% MoM" + (f"(예상 {sgn(c_h)}%) {word(cards[0]['value'], c_h)}" if c_h is not None else "")
    s2 = f"자동차 제외 {sgn(cards[1]['value'])}%" + (f"(예상 {sgn(c_x)}%)" if c_x is not None else "") + f", 컨트롤그룹 {sgn(cards[2]['value'])}%"
    if prior_before is not None and round(prior_before, 1) != round(cards[0]["prior"], 1):
        s3 = f"전월치 {prior_before:+.1f}% → {sgn(cards[0]['prior'])}%로 수정"
    else:
        s3 = f"전월치 {sgn(cards[0]['prior'])}% (수정 없음)"
    return {"id": "us-retail", "title": "미국 소매판매", "region": "US", "tier": 3, "period": month,
            "periodLabel": ko_month(month), "release": release, "releaseTime": "22:30 KST", "summary": [s1, s2, s3],
            "blocks": [{"type": "cards", "items": cards, "consSource": src},
                       {"type": "table", "title": "업태별 전월비 (%, SA)",
                        "columns": [{"label": ym(x)[2:], "d": 1, "s": True, "heat": True, "hl": x == month} for x in cols], "rows": rows},
                       ch] + ([reaction_table(D, release)] if reaction_table(D, release) else []),
            "sources": [{"name": "Census 소매판매", "url": "https://www.census.gov/retail/index.html"}]}


def briefing_claims(D: Data):
    meta = D.alfred.get("US_CLAIMS") or {}
    ic, ic4, cc = D.obs("ICSA"), D.obs("IC4WSA"), D.obs("CCSA")
    wk = ic[-1][0]
    release = meta.get("release") or ""
    c, src = D.consensus(release, "US_CLAIMS.icsa")
    if c is None:  # 목요일 발표 — 아카이브 키 날짜가 미국 날짜라 하루 어긋날 수 있음
        for k, v in D.cons.items():
            if k.endswith("|US_CLAIMS.icsa") and abs((dt.date.fromisoformat(k[:10]) - dt.date.fromisoformat(release or wk)).days) <= 1:
                c, src = v["forecast"], v.get("source")
    cards = [{"label": "신규 실업수당 청구", "value": ic[-1][1] / 1000, "d": 0, "u": "k", "cons": c / 1000 if c else None,
              "prior": ic[-2][1] / 1000},
             {"label": "4주 평균", "value": ic4[-1][1] / 1000, "d": 1, "u": "k", "prior": ic4[-2][1] / 1000},
             {"label": "계속 청구 (1주 시차)", "value": cc[-1][1] / 1000, "d": 0, "u": "k", "prior": cc[-2][1] / 1000,
              "foot": f"주간 {cc[-1][0]}"}]
    for x in cards:
        if x.get("cons") is not None:
            x["surprise"] = round(x["value"]) - round(x["cons"])
    xs = [d for d, _ in ic[-60:]]
    m4, mc = dict(ic4), dict(cc)
    ch = chart_block("신규 실업수당 (천명)", xs, [{"name": "신규", "values": [v / 1000 for _, v in ic[-60:]]},
                                              {"name": "4주 평균", "values": [m4[x] / 1000 if x in m4 else None for x in xs]}], unit="k", d=0)
    ch2 = chart_block("계속 실업수당 (천명)", xs, [{"name": "계속 청구", "values": [mc[x] / 1000 if x in mc else None for x in xs]}], unit="k", d=0)
    s1 = f"신규 청구 {ic[-1][1] / 1000:.0f}k" + (f"(예상 {c / 1000:.0f}k)" if c else "") + f", 4주 평균 {ic4[-1][1] / 1000:.1f}k"
    s2 = f"계속 청구 {cc[-1][1] / 1000:,.0f}k (전주 {(cc[-1][1] - cc[-2][1]) / 1000:+.0f}k)"
    yr = [v for d, v in ic if d >= add_months(wk[:8] + "01", -12)]
    s3 = f"최근 1년 범위 {min(yr) / 1000:.0f}–{max(yr) / 1000:.0f}k"
    return {"id": "us-claims", "title": "미국 주간 실업수당", "region": "US", "tier": 3, "period": wk,
            "periodLabel": f"{wk} 주간", "release": release, "releaseTime": "22:30 KST", "summary": [s1, s2, s3],
            "blocks": [{"type": "cards", "items": cards, "consSource": src}, ch, ch2],
            "sources": [{"name": "DOL 실업수당", "url": "https://www.dol.gov/ui/data.pdf"}]}


# ─────────────────────────── 캘린더
def build_calendar(D: Data):
    out = []
    for e in D.ff:
        if e["impact"] not in ("High", "Medium") or e["country"] not in ("USD", "KRW", "CNY", "EUR", "JPY"):
            continue
        if e["country"] != "USD" and e["impact"] != "High":
            continue
        t = dt.datetime.fromisoformat(e["date"]).astimezone(KST)
        out.append({"kst": t.strftime("%Y-%m-%d %H:%M"), "dow": "월화수목금토일"[t.weekday()], "country": e["country"],
                    "impact": e["impact"], "title": e["title"], "forecast": e["forecast"], "previous": e["previous"]})
    return sorted(out, key=lambda x: x["kst"])


def clean(o):
    """NaN/inf → None, float 은 6자리로 반올림(파일 크기)."""
    import math
    if isinstance(o, float):
        return None if math.isnan(o) or math.isinf(o) else round(o, 6)
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    return o


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/macro", help="raw/ 와 컨센서스 파일이 있는 폴더")
    ap.add_argument("--dest", default="", help="macro-data.js/.json 출력 폴더 (기본: --out)")
    a = ap.parse_args(argv)
    out = Path(a.out)
    dest = Path(a.dest) if a.dest else out
    dest.mkdir(parents=True, exist_ok=True)
    D = Data(out / "raw", out)
    briefs = []
    for fn in (briefing_cpi, briefing_nfp, briefing_fomc, briefing_exports, briefing_retail, briefing_claims):
        try:
            b = fn(D)
            if b:
                briefs.append(b)
        except Exception as e:  # 한 브리핑 실패가 전체를 막지 않도록
            import traceback
            traceback.print_exc()
            print(f"브리핑 실패 {fn.__name__}: {e}")
    briefs.sort(key=lambda b: b.get("release") or "", reverse=True)
    log = json.loads((out / "raw" / "collect_log.json").read_text()) if (out / "raw" / "collect_log.json").exists() else {}
    data = {"generatedAt": dt.datetime.now(KST).isoformat(timespec="minutes"),
            "collectedAt": log.get("ranAt"), "board": build_board(D), "calendar": build_calendar(D),
            "upcoming": build_upcoming(D),
            "briefings": briefs}
    js = json.dumps(clean(data), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    (dest / "macro-data.json").write_text(js)
    (dest / "macro-data.js").write_text("window.MACRO_DATA=" + js + ";\n")
    print(f"board {sum(len(g['tiles']) for g in data['board'])} tiles, briefings {[b['id'] for b in briefs]}, {len(js) / 1024:.0f} KB")


if __name__ == "__main__":
    main()
