"""화면용 데이터 파일 만들기(가볍게).

viewer/data/main.js   내부자 매매(하루·사람·유형 단위 묶음), 증여·그룹 내 이전, 거래계획, 종목 정보
viewer/data/bulk.js   외부 5% 대량보유(보고서 단위)
viewer/cdata/<코드>.js 기업 페이지: 종가 + 그 종목의 내부자 공시 낱개 행 + 지분율 보고
"""
import datetime as dt
import json
import re
from pathlib import Path

from .classify import INSIDER_REL

KST = dt.timezone(dt.timedelta(hours=9))


def _js(path: Path, var: str, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"window.{var}=" + json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + ";\n",
                    encoding="utf-8")


def _days(a, b):
    try:
        return (dt.date.fromisoformat(a) - dt.date.fromisoformat(b)).days
    except Exception:
        return 0


def kind_of(t):
    if t.get("transfer"):
        return "그룹 내 이전"
    if t.get("intra"):
        return "그룹 내 이동"
    ev, ch = t.get("event"), t.get("channel")
    tail = f" · {ch}" if ch and ch != "장내" else ""
    return {"GIFT_OUT": "증여", "GIFT_IN": "수증", "OPTION": "스톡옵션 행사", "PAID_IN": "유상증자 참여",
            "CONVERSION": "전환·행사"}.get(ev) or ("매수 · 사채 경유" if t.get("via") else
                                                 "매수" + tail if ev == "BUY" else "매도" + tail if ev == "SELL" else "기타")


def mark_of(t):
    if t.get("view") != "insider":
        return None
    if t.get("transfer"):
        return "transfer"
    if t.get("signal") == "gift":
        return "gift"
    if t.get("signal") == "trade":
        return "buy" if t.get("direction") == "buy" else "sell"
    return None


def pct(t):
    return round(t["qty_change"] / t["shares"] * 100, 5) if t.get("shares") and t.get("qty_change") else None


def r2(x, n=0):
    return None if x is None else (round(x, n) if n else round(x))


# ── 내부자 매매: 같은 종목·사람·변동일·유형을 한 줄로 ──
TCOLS = ["code", "person", "rel", "pos", "grp", "date", "rdt", "rno", "dir", "kind",
         "n", "qty", "amt", "pct", "after", "loan", "lag"]


def build_trades(txs):
    agg = {}
    for t in txs:
        if t.get("signal") != "trade" or t.get("view") != "insider":
            continue
        k = (t.get("stock_code"), t.get("person"), t.get("date"), kind_of(t), t.get("direction"))
        a = agg.get(k)
        if not a:
            a = agg[k] = dict(code=t.get("stock_code"), person=t.get("person"), rel=t.get("relation"),
                              pos=t.get("position"), grp=t.get("holder_group"), date=t.get("date"),
                              rdt=t["rcept_dt"], rno=t["rcept_no"], dir=t.get("direction"), kind=k[3],
                              n=0, qty=0, amt=0, has_amt=False, pct=0, after=t.get("pct_after"), loan=0, lag=0)
        a["n"] += 1
        a["qty"] += t["qty_change"]
        if t.get("amount"):
            a["amt"] += t["amount"]
            a["has_amt"] = True
        a["pct"] += pct(t) or 0
        if t["rcept_no"] >= a["rno"]:
            a["rno"], a["rdt"] = t["rcept_no"], t["rcept_dt"]
            if t.get("pct_after") is not None:
                a["after"] = t["pct_after"]
        if (t.get("funding") or {}).get("loan"):
            a["loan"] = max(a["loan"], t["funding"]["loan"])
        a["lag"] = max(a["lag"], _days(t["rcept_dt"], t.get("date") or t["rcept_dt"]))
    rows = []
    for a in agg.values():
        a["amt"] = r2(a["amt"]) if a.pop("has_amt") else None
        a["pct"] = r2(a["pct"], 5)
        a["loan"] = a["loan"] or None
        rows.append([a[c] for c in TCOLS])
    rows.sort(key=lambda r: (r[6], r[7]), reverse=True)
    return rows


# ── 증여·그룹 내 이전 ──
def build_gifts(txs):
    out = []
    for t in txs:
        if t.get("signal") != "gift" or t.get("view") != "insider":
            continue
        tr = bool(t.get("transfer"))
        outgoing = tr or t.get("event") == "GIFT_OUT"
        if outgoing:
            rec = t.get("recipients") or ([{"person": t["counterparty"]}] if t.get("counterparty") else [])
        else:
            rec = [{"person": t.get("person"), "relation": t.get("relation"), "qty": t.get("qty_change")}]
        out.append(dict(
            code=t.get("stock_code"), rdt=t["rcept_dt"], rno=t["rcept_no"], date=t.get("date"),
            kind="그룹 내 이전" if tr else "증여",
            giver=t.get("giver_names") if tr else (t.get("person") if outgoing else t.get("counterparty") or ""),
            giverRel=" · ".join(x for x in (t.get("relation"), t.get("position")) if x) if outgoing else "",
            grp=t.get("holder_group"),
            rec=[[x.get("person"), x.get("relation"), x.get("qty")] for x in rec],
            qty=t.get("transfer_qty") if tr else abs(t.get("qty_change") or 0),
            amt=r2(t.get("transfer_amount") if tr else t.get("amount")),
            pct=r2(t.get("transfer_pct") if tr else abs(pct(t) or 0), 5),
            giverPct=t.get("pct_after") if outgoing else None,
            note=t.get("note"), lag=_days(t["rcept_dt"], t.get("date") or t["rcept_dt"]),
        ))
    out.sort(key=lambda g: (g["rdt"], g["rno"]), reverse=True)
    return out


def build_plans(plans):
    keep = ("stock_code", "rcept_dt", "rcept_no", "person", "relation", "position", "method", "sign",
            "security", "start", "end", "qty", "price", "amount", "plan_ratio", "purpose_text")
    return [{k: p.get(k) for k in keep if p.get(k) not in (None, "")} for p in plans]


# ── 외부 5% 대량보유 ──
BCOLS = ["code", "rdt", "rno", "form", "filer", "rtype", "purpose", "rkind", "before", "after",
         "buy", "sell", "n", "first", "last", "method", "reason", "flags"]


def build_bulk(filings, txs):
    rows_by = {}
    for t in txs:
        if t["form"] in ("BULK_G", "BULK_S") and t.get("qty_change"):
            rows_by.setdefault(t["rcept_no"], []).append(t)
    out = []
    for f in filings:
        if f["form"] not in ("BULK_G", "BULK_S"):
            continue
        rep = f.get("reporter") or {}
        rel = rep.get("relation") or ""
        if not f.get("external") and any(k in rel for k in INSIDER_REL):
            continue  # 최대주주 측·임원은 내부자 매매 쪽
        rs = rows_by.get(f["rcept_no"], [])
        buy = sum(t["qty_change"] for t in rs if t["qty_change"] > 0)
        sell = -sum(t["qty_change"] for t in rs if t["qty_change"] < 0)
        dates = sorted(t["date"] for t in rs if t.get("date"))
        b, a = f.get("group_pct_before"), f.get("group_pct_after")
        purpose = f.get("purpose") or ""
        reason = f.get("reason_text") or ""
        kind = rep.get("report_kind") or ""
        flags = []
        if "신규" in kind or (b is not None and a is not None and b < 5 <= a):
            flags.append("신규")
        if a is not None and a < 5:
            flags.append("이탈")
        if "목적" in reason and ("변경" in reason or "변경" in kind):
            flags.append("목적변경")
        if "경영권" in purpose:
            flags.append("경영권")
        out.append([f.get("stock_code"), f["rcept_dt"], f["rcept_no"], "약식" if f["form"] == "BULK_S" else "일반",
                    f.get("filer"), rep.get("type"), purpose or None, kind or None, b, a,
                    buy or None, sell or None, len(rs) or None,
                    dates[0] if dates else None, dates[-1] if dates else None,
                    f.get("change_method"), (reason[:200] or None), flags])
    out.sort(key=lambda r: (r[1], r[2]), reverse=True)
    return out


# ── 기업 페이지 상세 ──
def company_rows(rows):
    out = []
    for t in rows:
        mk = mark_of(t)
        tr = bool(t.get("transfer"))
        out.append({k: v for k, v in dict(
            rdt=t["rcept_dt"], rno=t["rcept_no"], date=t.get("date"),
            person=t.get("giver_names") if tr else t.get("person"),
            rel=t.get("relation"), pos=t.get("position"), grp=t.get("holder_group"),
            kind=kind_of(t), mark=mk, cp=t.get("counterparty"),
            qty=-(t.get("transfer_qty") or 0) if tr else t.get("qty_change"),
            price=t.get("price") or t.get("price_est"),
            amt=r2(t.get("transfer_amount") if tr else t.get("amount")),
            pct=r2(-(t.get("transfer_pct") or 0) if tr else pct(t), 5),
            after=t.get("pct_after"), note=t.get("note"),
            loan=(t.get("funding") or {}).get("loan"),
        ).items() if v not in (None, "")})
    return out


def write_outputs(root: Path, start, end, filings, txs, plans, sectors, prices, log=print):
    viewer = root / "viewer"
    now = dt.datetime.now(KST).isoformat(timespec="seconds")
    price_last = max((p["d"][-1] for p in prices.values() if p.get("d")), default=None)
    meta = dict(generated_at=now, range=[start.isoformat(), end.isoformat()], price_last=price_last)

    corps = {}
    for t in [*txs, *plans, *filings]:
        c = t.get("stock_code")
        if c and c not in corps:
            sec = sectors.get(c) or (None, None)
            corps[c] = [t.get("corp_name"), t.get("market"), sec[0], sec[1]]

    trades = build_trades(txs)
    gifts = build_gifts(txs)
    main = dict(meta=meta, corps=corps, tcols=TCOLS, trades=trades, gifts=gifts, plans=build_plans(plans))
    _js(viewer / "data" / "main.js", "D", main)

    bulk = build_bulk(filings, txs)
    bcorps = {r[0]: corps.get(r[0]) for r in bulk if r[0]}
    _js(viewer / "data" / "bulk.js", "B", dict(meta=meta, corps=bcorps, cols=BCOLS, rows=bulk))

    # 기업별 파일
    by_code = {}
    for t in txs:
        if t.get("view") == "insider" and t.get("stock_code"):
            by_code.setdefault(t["stock_code"], []).append(t)
    for p in plans:
        if p.get("stock_code"):
            by_code.setdefault(p["stock_code"], [])
    hold = {}
    for f in filings:
        if f.get("group_pct_after") is not None and not f.get("external") and f["form"] in ("BULK_G", "MAJOR"):
            hold.setdefault(f.get("stock_code"), []).append(
                dict(rdt=f["rcept_dt"], rno=f["rcept_no"], form=f["form_label"],
                     filer=(f.get("reporter") or {}).get("name") or f.get("filer"),
                     pct=f["group_pct_after"]))
    bulk_by = {}
    for r in bulk:
        if r[0]:
            bulk_by.setdefault(r[0], []).append(r)
            by_code.setdefault(r[0], [])
    plan_by = {}
    for p in build_plans(plans):
        plan_by.setdefault(p.get("stock_code"), []).append(p)
    for code, rows in by_code.items():
        rows.sort(key=lambda t: (t.get("date") or "", t["rcept_no"]), reverse=True)
        _js(viewer / "cdata" / f"{code}.js", "C", dict(
            meta=meta, code=code, corp=corps.get(code), prices=prices.get(code),
            rows=company_rows(rows), plans=plan_by.get(code, []),
            bcols=BCOLS, bulk=sorted(bulk_by.get(code, []), key=lambda r: r[2], reverse=True),
            holdings=sorted(hold.get(code, []), key=lambda h: h["rno"], reverse=True)))

    # 기업 페이지 종목 검색용 목록 (코드, 종목명)
    _js(viewer / "cdata" / "index.js", "CIDX", sorted([code, (corps.get(code) or [code])[0]] for code in by_code))

    size = lambda p: (viewer / p).stat().st_size / 1e6
    log(f"화면 데이터: 매매 {len(trades)}줄·증여 {len(gifts)}건 ({size('data/main.js'):.1f}MB), "
        f"대량보유 {len(bulk)}건 ({size('data/bulk.js'):.1f}MB), 기업 파일 {len(by_code)}개")


def review_log(txs, end, days=7, log=print, limit=15):
    """최근 공시 중 '기타'·'사채·증자'로 빠진 내부자 행: 규칙 점검용."""
    cut = (end - dt.timedelta(days=days)).isoformat()
    rows = [t for t in txs if t.get("view") == "insider" and t["rcept_dt"] >= cut
            and t.get("direction") in ("other", "caution") and not t.get("via")]
    if not rows:
        return
    log(f"점검 필요(최근 {days}일, 기타·사채·증자 {len(rows)}행):")
    for t in rows[:limit]:
        log(f"  {t['rcept_dt']} {t['corp_name']} {t.get('person')} {t.get('reason')} "
            f"{t.get('qty_change')} | {(t.get('note') or '')[:40]} | {t['url']}")
