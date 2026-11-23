"""보고서 양식별 파서 → 거래(변동) 행 리스트.

대상 양식
  EXEC   임원ㆍ주요주주 특정증권등 소유상황보고서       (XML, 코드 셀)
  BULK_G 주식등의 대량보유상황보고서(일반)              (XML, 코드 셀)
  BULK_S 주식등의 대량보유상황보고서(약식)              (XML, 코드 셀)
  PLAN   임원ㆍ주요주주 특정증권등 거래계획보고서       (XML, 코드 셀)
  MAJOR  최대주주등소유주식변동신고서(거래소 공시)      (HTML 양식)
"""
import re

from bs4 import BeautifulSoup

from .xforms import decode, first, groups, num, ymd

FORM_LABEL = {
    "EXEC": "임원·주요주주 소유보고",
    "BULK_G": "대량보유(일반)",
    "BULK_S": "대량보유(약식)",
    "PLAN": "거래계획보고",
    "MAJOR": "최대주주등 변동신고",
}


def form_of(report_nm: str):
    n = report_nm.replace(" ", "")
    if "특정증권등소유상황보고서" in n:
        return "EXEC"
    if "특정증권등거래계획보고서" in n:
        return "PLAN"
    if "대량보유상황보고서(일반)" in n:
        return "BULK_G"
    if "대량보유상황보고서(약식)" in n:
        return "BULK_S"
    if "최대주주등소유주식변동신고서" in n:
        return "MAJOR"
    return None


def _dash(v):
    return None if v in (None, "", "-") else v


def _security(label):
    label = (label or "").strip()
    if label in ("보통주", "보통주식", "의결권있는 주식", "의결권 있는 주식"):
        return "보통주", "stock"
    if "우선주" in label or label in ("종류주식",):
        return "우선주", "stock"
    if label in ("", "-"):
        return None, "stock"
    return label, "other"


def _tx(**kw):
    base = dict(
        person=None, person_id=None, person_type=None, relation=None,
        position=None, date=None, reason=None, reason_code=None,
        security=None, security_kind="stock", qty_before=None,
        qty_change=None, qty_after=None, price=None, note=None,
        pct_after=None,
    )
    base.update(kw)
    return base


# ───────────────────────── EXEC ─────────────────────────
def parse_exec(text: str) -> dict:
    g = groups(text)
    reg = first(g, "STF_RYN@")
    rel = []
    if reg == "Y":
        rel.append("등기임원")
    elif reg == "N":
        rel.append("미등기임원")
    main = first(g, "MAIN_SH")
    if main and main != "-":
        rel.append(main)
    reporter = dict(
        name=first(g, "IFR_NM"),
        id=first(g, "IFR_ID2"),
        type=first(g, "IFR_TP"),
        type_code=first(g, "IFR_TP@"),
        relation="·".join(rel) or None,
        position=_dash(first(g, "STF_PSM")),
        exec_registered=reg,
        report_kind=first(g, "RPT_DST"),
    )
    shares = num(first(g, "FLT_SUM")) or num(first(g, "VOT_STK_CNT"))
    pct_after = num(first(g, "AFR_PS_CPT_RT"))
    txs = []
    for rows in g.values():
        for r in rows:
            if "RPT_RSN" not in r:
                continue
            chg = num(r.get("MDF_STK_CNT"))
            sec, kind = _security(r.get("STR_KND"))
            txs.append(_tx(
                person=reporter["name"], person_id=reporter["id"],
                person_type=reporter["type"], relation=reporter["relation"],
                position=reporter["position"],
                date=ymd(r.get("MDF_DM@") or r.get("MDF_DM")),
                reason=r.get("RPT_RSN"), reason_code=r.get("RPT_RSN@"),
                security=sec, security_kind=kind,
                qty_before=num(r.get("BFR_STK_CNT")), qty_change=chg,
                qty_after=num(r.get("AFR_STK_CNT")),
                price=num(r.get("ACI_AMT2")), note=_dash(r.get("RMK")),
                pct_after=pct_after,
                plan_report_date=ymd(r.get("TRD_PLN_RD@")),
            ))
    return dict(reporter=reporter, shares=shares, purpose=None, txs=txs, funding=[])


# ───────────────────────── BULK (일반·약식) ─────────────────────────
def _norm_id(v):
    return re.sub(r"\D", "", v or "") or None


def parse_bulk(text: str, simple: bool) -> dict:
    g = groups(text)
    # 보고자 본인
    rep_rel = None
    for r in g.get("RPT_DST1", []) + g.get("SUM_INF", []):
        if "FLT_CRP_RLT" in r and r.get("FLT_CRP_RLT") not in (None, "-"):
            rep_rel = r["FLT_CRP_RLT"]
            break
    reporter = dict(
        name=first(g, "RPT_RSP_NM") or first(g, "IFR_NM"),
        id=first(g, "IFR_ID"),
        type=first(g, "CRP_TP"),
        type_code=first(g, "CRP_TP@"),
        relation=rep_rel,
        position=_dash(first(g, "IFR_JOB")),
        report_kind=first(g, "RPT_DST1"),
    )
    purpose = first(g, "PUR_OWN") or ("단순·일반투자(약식)" if simple else None)

    # 특별관계자: 이름/ID → (발행회사와의 관계, 보고자와의 관계, 직업)
    people = {}
    if reporter["name"]:
        people[_norm_id(reporter["id"]) or reporter["name"]] = dict(
            relation=reporter["relation"], to_reporter="본인",
            job=reporter["position"], type=reporter["type"])
    for rows in g.values():
        for r in rows:
            if "SPC_NM" in r and "SPC_TP" in r and _dash(r.get("SPC_NM")):
                people[_norm_id(r.get("SPC_ID2")) or r["SPC_NM"]] = dict(
                    relation=_dash(r.get("FLT_CRP_RLT")),
                    to_reporter=_dash(r.get("IFR_RLT")),
                    job=_dash(r.get("JOB")), type=_dash(r.get("SPC_TP")))

    def who(name, pid):
        return people.get(_norm_id(pid)) or people.get(name) or {}

    # 개인별 보유비율(이번 보고서)
    pct = {}
    for r in g.get("CST_CNT1", []):
        if _dash(r.get("SPC_NM")):
            pct[_norm_id(r.get("SPC_ID2")) or r["SPC_NM"]] = num(r.get("STK_RT"))

    txs = []
    for rows in g.values():
        for r in rows:
            if "HLD_MTH" not in r or not _dash(r.get("SPC_NM")):
                continue
            chg = num(r.get("MDF_SDK_CNT"))
            if chg is None and not _dash(r.get("HLD_MTH")):
                continue
            w = who(r["SPC_NM"], r.get("SPC_ID2"))
            sec, kind = _security(r.get("STK_KND"))
            rel = w.get("relation")
            to = w.get("to_reporter")
            if to and to not in ("본인", "기타") and to != rel:
                rel = f"{rel}({to})" if rel else to
            txs.append(_tx(
                person=r["SPC_NM"], person_id=_dash(r.get("SPC_ID2")),
                person_type=w.get("type"), relation=rel, position=w.get("job"),
                date=ymd(r.get("MDF_DT@") or r.get("MDF_DT")),
                reason=r.get("HLD_MTH"), reason_code=r.get("HLD_MTH@"),
                security=sec, security_kind=kind,
                qty_before=num(r.get("BFR_MDF_CNT")), qty_change=chg,
                qty_after=num(r.get("AFR_MDF_CNT")),
                price=num(r.get("HLD_UNT_PRJ")), note=_dash(r.get("RMK")),
                pct_after=pct.get(_norm_id(r.get("SPC_ID2")) or r["SPC_NM"]),
            ))

    # 취득자금 조성내역
    detail = {}
    for cls, rows in g.items():
        if cls.startswith("CSE"):
            for r in rows:
                txt = " ".join(v for k, v in r.items()
                               if k.endswith("_DTL") and _dash(v))
                if txt:
                    key = _norm_id(r.get("PRH_ID")) or r.get("PRH_NM")
                    detail[key] = (detail.get(key, "") + " " + txt).strip()
    funding = []
    for rows in g.values():
        for r in rows:
            if "PRH_NM" in r and "PRH_CPT" in r and _dash(r.get("PRH_NM")):
                key = _norm_id(r.get("PRH_ID")) or r["PRH_NM"]
                funding.append(dict(
                    person=r["PRH_NM"], person_id=_dash(r.get("PRH_ID")),
                    own=num(r.get("PRH_CPT")), loan=num(r.get("PRH_LN")),
                    other=num(r.get("PRH_AMT")), total=num(r.get("PRH_SUM")),
                    detail=detail.get(key),
                ))
    shares = num(first(g, "FLT_STK_CNT")) or num(first(g, "VOT_STK_CNT"))
    reason_text = " / ".join(x for x in (_dash(first(g, "SUM_CHN_RWN")), _dash(first(g, "CHN_RSN")),
                                         _dash(first(g, "CHN_RSM"))) if x)
    return dict(reporter=reporter, shares=shares, purpose=purpose, txs=txs,
                funding=funding,
                group_pct_before=num(first(g, "BFR_STK_RT")),
                group_pct_after=num(first(g, "THS_STK_RT")),
                change_method=_dash(first(g, "CHN_HOW")),
                reason_text=reason_text or None)


# ───────────────────────── PLAN ─────────────────────────
def parse_plan(text: str) -> dict:
    g = groups(text)
    reg = first(g, "STF_RYN@")
    rel = []
    if reg == "Y":
        rel.append("등기임원")
    elif reg == "N":
        rel.append("미등기임원")
    main = first(g, "MAIN_SH")
    if main and main != "-":
        rel.append(main)
    reporter = dict(
        name=first(g, "IFR_NM"), id=first(g, "IFR_ID2"),
        type=first(g, "IFR_TP"), type_code=first(g, "IFR_TP@"),
        relation="·".join(rel) or None, position=_dash(first(g, "STF_PSM")),
    )
    plans = []
    for rows in g.values():
        for r in rows:
            if "MDF_STR_DT" not in r or not _dash(r.get("MDF_MT")):
                continue
            sec, kind = _security(r.get("STR_KND"))
            plans.append(dict(
                start=ymd(r.get("MDF_STR_DT@")), end=ymd(r.get("MDF_END_DT@")),
                method=r.get("MDF_MT"), method_code=r.get("MDF_MT@"),
                security=sec, security_kind=kind,
                qty=num(r.get("STR_STK_CNT")), price=num(r.get("ACI_AMT2")),
                amount=num(r.get("TRAN_AMT")), note=_dash(r.get("RMK")),
            ))
    return dict(
        reporter=reporter,
        shares=num(first(g, "FLT_SUM")),
        purpose=_dash(first(g, "TRAN_PPS")),
        plan_ratio=num(first(g, "STR_STK_RT")),
        contract=_dash(first(g, "CTR_DTL")),
        trade_start=ymd(first(g, "RPT_STR_DT@")),
        plans=plans, txs=[], funding=[],
    )


# ───────────────────────── MAJOR (HTML) ─────────────────────────
_DATE = re.compile(r"^\d{4}[-.]\d{2}[-.]\d{2}")


def parse_major(text: str) -> dict:
    soup = BeautifulSoup(text, "html.parser")
    tables = soup.find_all("table")

    def cells(tr):  # 양식 표가 중첩돼 있어 직속 셀만 읽는다
        return [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"], recursive=False)]

    shares = None
    for t in tables:
        if re.search(r"_Table1$", t.get("id") or ""):
            rs = [cells(tr) for tr in t.find_all("tr")]
            if len(rs) >= 2 and len(rs[1]) >= 3:
                shares = num(rs[1][2])

    group_pct, seen_now = None, False
    for t in tables:
        if re.search(r"_Table2$", t.get("id") or ""):
            for tr in t.find_all("tr"):
                c = cells(tr)
                if c and c[0].startswith("이번보고서"):
                    seen_now = True
                elif seen_now and c and c[0] == "합계" and len(c) >= 3:
                    group_pct = num(c[2])
                    break

    # 변동 후 개인별 보유 표와 개인별 변동 표: 중첩 표 때문에 문서의 모든 행을 한 번씩만 훑는다
    holdings, txs, cur, seen = {}, [], {}, set()
    for tr in soup.find_all("tr"):
        c = cells(tr)
        if not c:
            continue
        if (len(c) >= 11 and c[0] not in ("성명", "합계", "주식수")
                and ("번호" in c[1] or "생년월일" in c[1])):
            holdings[c[0]] = dict(id=c[2], relation=c[6], qty=num(c[9]), pct=num(c[10]))
        elif c[0] == "성명" and len(c) in (4, 5, 6) and ("번호" in c[2] or "생년월일" in c[2]):
            cur = dict(name=c[1], id=c[3])
        elif c[0].startswith("국내외") and len(c) > 1:
            cur["type"] = c[1]
        elif c[0].startswith("최대주주") and len(c) > 1:
            cur["relation"] = c[1]
        elif 6 <= len(c) <= 8 and _DATE.match(c[0]):
            key = (cur.get("name"), cur.get("id"), *c[:6])
            if key in seen:  # 같은 표가 두 번 실린 경우
                continue
            seen.add(key)
            sec, kind = _security(c[2])
            txs.append(_tx(
                person=cur.get("name"), person_id=cur.get("id"),
                person_type=cur.get("type"), relation=cur.get("relation"),
                date=ymd(c[0]), reason=c[1], security=sec,
                security_kind=kind, qty_before=num(c[3]),
                qty_change=num(c[4]), qty_after=num(c[5]),
                note=_dash(c[6]) if len(c) > 6 else None,
            ))
    for t in txs:
        t["pct_after"] = holdings.get(t["person"], {}).get("pct")
    me = next((dict(name=k, id=v["id"], relation="최대주주(본인)")
               for k, v in holdings.items() if v.get("relation") == "본인"),
              dict(name=None))
    for t in txs:
        if t["relation"] == "본인":
            t["relation"] = "최대주주(본인)"
    return dict(reporter=me, shares=shares, purpose=None, txs=txs, funding=[],
                group_pct_after=group_pct)


def parse(form: str, raw: bytes) -> dict:
    text = decode(raw)
    if form == "EXEC":
        return parse_exec(text)
    if form == "BULK_G":
        return parse_bulk(text, simple=False)
    if form == "BULK_S":
        return parse_bulk(text, simple=True)
    if form == "PLAN":
        return parse_plan(text)
    if form == "MAJOR":
        return parse_major(text)
    raise ValueError(form)
