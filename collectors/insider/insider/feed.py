"""여러 날 파일을 모아 중복·정정 처리, 증여 짝짓기 후 뷰어용 feed를 만든다."""
import datetime as dt
import gzip
import json
import re
from pathlib import Path

FORM_RANK = {"EXEC": 1, "BULK_G": 2, "MAJOR": 3, "BULK_S": 4}
# 뷰어로 보낼 거래 필드


def norm_name(s):
    s = s or ""
    s = re.sub(r"\(.*?\)|주식회사|㈜|\s", "", s)
    return s.upper()


def load_days(root: Path, start: dt.date, end: dt.date):
    days = []
    d = start
    while d <= end:
        p = root / "data" / "days" / str(d.year) / f"{d:%Y%m%d}.json.gz"
        if p.exists():
            days.append(json.loads(gzip.decompress(p.read_bytes())))
        d += dt.timedelta(days=1)
    return days


def mark_superseded(filings, txs):
    """정정 전 원본 보고서(목록 비고 '정')의 거래 행을 dup 처리.
    정정본과 내용이 같은 행은 dedup()에서 최신 접수번호가 남는다."""
    sup = {f["rcept_no"] for f in filings if f.get("corrected_later")}
    for t in txs:
        if t["rcept_no"] in sup and t["view"] != "dup":
            t["view"], t["why"] = "dup", "정정 전 원본"
    return sup


def dedup(txs):
    """같은 사람·같은 날·같은 방향(+/-)·같은 수량이 여러 보고서에 실린 경우 하나만 남긴다."""
    groups = {}
    for t in txs:
        if t["view"] == "dup" or not t.get("qty_change"):
            continue
        k = (t.get("stock_code") or t["corp_name"], norm_name(t.get("person")),
             t.get("date"), 1 if t["qty_change"] > 0 else -1, abs(t["qty_change"]))
        groups.setdefault(k, []).append(t)
    n = 0
    for g in groups.values():
        if len(g) < 2:
            continue
        # 같은 보고서 안의 같은 행(분할 체결 등)은 중복이 아님
        if len({t["rcept_no"] for t in g}) < 2:
            continue
        g.sort(key=lambda t: (FORM_RANK.get(t["form"], 9), -int(t["rcept_no"])))
        keep = g[0]
        seen_rcpt = {keep["rcept_no"]}
        for t in g[1:]:
            if t["rcept_no"] in seen_rcpt:
                continue
            seen_rcpt.add(t["rcept_no"])
            for f in ("price", "amount", "relation", "position", "pct_after", "funding"):
                if keep.get(f) in (None, "") and t.get(f) not in (None, ""):
                    keep[f] = t[f]
            t["view"], t["why"], t["dup_of"] = "dup", f"중복({keep['form_label']})", keep["id"]
            n += 1
    return n + dedup_shifted(txs)


def dedup_shifted(txs, max_days=5):
    """대량보유보고서는 장내매매 변동일을 '계약체결일'로, 임원보고·최대주주변동신고서는 '결제일'로 적어
    같은 거래의 날짜가 이틀가량 어긋난다. 양식이 다른 두 보고서 사이에서, 같은 사람·방향·수량이고
    날짜 차이가 max_days일 이내인 행을 짝지어 하나만 남긴다(가까운 날짜끼리 먼저).
    경영권 인수처럼 큰 장외거래는 주식매매계약일과 종결일이 몇 달 떨어지므로 150일까지 본다."""
    groups = {}
    for t in txs:
        if t["view"] == "dup" or not t.get("qty_change") or not t.get("date"):
            continue
        k = (t.get("stock_code") or t["corp_name"], norm_name(t.get("person")),
             1 if t["qty_change"] > 0 else -1, abs(t["qty_change"]))
        groups.setdefault(k, []).append(t)
    n = 0
    for g in groups.values():
        if len({t["form"] for t in g}) < 2:
            continue
        pairs = []
        for i, a in enumerate(g):
            for b in g[i + 1:]:
                if a["form"] == b["form"] or a["rcept_no"] == b["rcept_no"]:
                    continue
                gap = abs((dt.date.fromisoformat(a["date"]) - dt.date.fromisoformat(b["date"])).days)
                # 발행주식 0.5% 이상인 큰 거래는 계약일(대량보유)과 거래종결일(임원보고)이 몇 달 벌어질 수 있다
                shares = a.get("shares") or b.get("shares")
                big = shares and abs(a["qty_change"]) / shares >= 0.005
                if 0 < gap <= (150 if big else max_days):
                    pairs.append((gap, a, b))
        pairs.sort(key=lambda x: x[0])
        for _, a, b in pairs:
            if a["view"] == "dup" or b["view"] == "dup":
                continue
            keep, drop = sorted((a, b), key=lambda t: (FORM_RANK.get(t["form"], 9), -int(t["rcept_no"])))
            for f in ("price", "amount", "relation", "position", "pct_after", "funding"):
                if keep.get(f) in (None, "") and drop.get(f) not in (None, ""):
                    keep[f] = drop[f]
            drop["view"], drop["dup_of"] = "dup", keep["id"]
            drop["why"] = f"중복({keep['form_label']}, 날짜 표기 차이)"
            n += 1
    return n + dedup_big_any_name(txs)


def dedup_big_any_name(txs, max_days=150):
    """발행주식 0.5% 이상인 큰 거래는 수량이 사실상 고유하다. 보고자 이름이 보고서마다 다르게
    적혀도(한글·영문 표기, 약칭) 같은 종목·방향·수량이고 날짜가 가까우면 같은 거래로 본다."""
    groups = {}
    for t in txs:
        q, sh = t.get("qty_change"), t.get("shares")
        if t["view"] == "dup" or not q or not sh or abs(q) / sh < 0.005 or not t.get("date"):
            continue
        groups.setdefault((t.get("stock_code") or t["corp_name"], q), []).append(t)
    n = 0
    for g in groups.values():
        if len({t["rcept_no"] for t in g}) < 2:
            continue
        g.sort(key=lambda t: (FORM_RANK.get(t["form"], 9), -int(t["rcept_no"])))
        keep = g[0]
        for t in g[1:]:
            if t["view"] == "dup" or t["rcept_no"] == keep["rcept_no"]:
                continue
            gap = abs((dt.date.fromisoformat(t["date"]) - dt.date.fromisoformat(keep["date"])).days)
            if gap > max_days:
                continue
            for f in ("price", "amount", "relation", "position", "pct_after", "funding"):
                if keep.get(f) in (None, "") and t.get(f) not in (None, ""):
                    keep[f] = t[f]
            t["view"], t["dup_of"] = "dup", keep["id"]
            t["why"] = f"중복({keep['form_label']}, 큰 거래·표기 차이)"
            n += 1
    return n


OFF_MARKET = ("시간외", "장외", "대량매매")


def mark_intra_group(txs):
    """같은 종목·같은 날, 같은 보고 그룹(한 보고서에 함께 실린 특수관계인)끼리 시간외·장외·대량매매로
    주고받은 거래를 '그룹 내 이동'으로 묶어 매매 신호에서 뺀다. 판 수량과 산 수량이 1:1로 같거나,
    한쪽 1명과 다른 쪽 여러 명의 합이 같을 때. 양쪽 단가가 모두 있으면 같아야 한다.
    서로 다른 보고서에만 실린 상대방(예: 경영권 인수자)은 그룹 밖 거래로 보고 묶지 않는다."""
    from itertools import combinations
    # 거래 행이 실린 보고서들(중복 처리로 숨은 사본 포함)
    seen_in = {}
    for t in txs:
        if t.get("qty_change"):
            k = (t.get("stock_code") or t["corp_name"], t.get("date"), norm_name(t.get("person")), t["qty_change"])
            seen_in.setdefault(k, set()).add(t["rcept_no"])

    def filings_of(t):
        return seen_in.get((t.get("stock_code") or t["corp_name"], t.get("date"),
                            norm_name(t.get("person")), t["qty_change"]), {t["rcept_no"]})

    def new_big_holder(t):
        # 기존 보유 없이 발행주식 1% 이상을 받는 쪽 = 경영권 인수자 등 그룹 밖 상대로 본다
        return (t["qty_change"] > 0 and not t.get("qty_before")
                and t.get("shares") and t["qty_change"] / t["shares"] >= 0.01)

    def same_group(one, others):
        if any(new_big_holder(t) for t in [one, *others]):
            return False
        common = set(filings_of(one))
        for o in others:
            common &= filings_of(o)
        return bool(common)
    pool = {}
    for t in txs:
        if (t.get("view") == "insider" and t.get("signal") == "trade"
                and t.get("channel") in OFF_MARKET and t.get("qty_change")):
            pool.setdefault((t.get("stock_code") or t["corp_name"], t.get("date")), []).append(t)

    def same_price(a, others):
        return all(not a.get("price") or not o.get("price") or a["price"] == o["price"] for o in others)

    def link(one, many):
        for t in [one, *many]:
            t["signal"], t["intra"] = None, True
            t["why"] = "그룹 내 이동"
            t["intra_id"] = one["id"]
        one["counterparty"] = ", ".join(o.get("person") or "" for o in many)
        for o in many:
            o["counterparty"] = one.get("person")

    n = 0
    for rows in pool.values():
        sells = [t for t in rows if t["qty_change"] < 0]
        buys = [t for t in rows if t["qty_change"] > 0]
        if not sells or not buys:
            continue
        for side, other in ((sells, buys), (buys, sells)):
            for one in side:
                if one.get("intra"):
                    continue
                q = abs(one["qty_change"])
                free = [o for o in other if not o.get("intra")]
                hit = next((o for o in free if abs(o["qty_change"]) == q and same_price(one, [o])
                            and same_group(one, [o])), None)
                if hit:
                    link(one, [hit]); n += 2
                    continue
                for k in range(2, min(4, len(free)) + 1):
                    combo = next((c for c in combinations(free, k)
                                  if sum(abs(o["qty_change"]) for o in c) == q and same_price(one, c)
                                  and same_group(one, c)), None)
                    if combo:
                        link(one, list(combo)); n += 1 + k
                        break
    return n


FUND_NAME = re.compile(r"자산운용|투자자문|펀드|FUND|L\.?P\.?$|\bLP\b|투자조합|사모|PEF|ASSET", re.I)
CORP_NAME = re.compile(r"\(주\)|㈜|주식회사|유한회사|홀딩스|재단|법인|CORP|INC|LTD|CO\.", re.I)


def _is_corp(t):
    return "법인" in (t.get("person_type") or "") or bool(CORP_NAME.search(t.get("person") or ""))


def _birth_year(t):
    d = re.sub(r"\D", "", t.get("person_id") or "")
    if _is_corp(t) or len(d) != 6:
        return None
    yy = int(d[:2])
    return 2000 + yy if yy <= dt.date.today().year % 100 else 1900 + yy


def mark_transfers(txs, min_pct=0.5, min_amount=10e8):
    """그룹 내 이동 중 승계·지배구조 개편으로 볼 만한 큰 건을 증여 화면용 '그룹 내 이전'으로 올린다.
    조건: 지분 0.5%p 이상 또는 10억 원 이상, 펀드·운용사 사이 이동 제외,
    받는 쪽이 회사이거나(개인→회사, 회사→회사) 개인끼리면 받는 쪽이 아랫세대일 때."""
    events = {}
    for t in txs:
        if t.get("intra_id"):
            events.setdefault(t["intra_id"], []).append(t)
    n = 0
    for rows in events.values():
        sellers = [t for t in rows if t["qty_change"] < 0]
        buyers = [t for t in rows if t["qty_change"] > 0]
        if not sellers or not buyers:
            continue
        if any(FUND_NAME.search(t.get("person") or "") for t in rows):
            continue
        qty = sum(-t["qty_change"] for t in sellers)
        shares = next((t.get("shares") for t in rows if t.get("shares")), None)
        pct = qty / shares * 100 if shares else 0
        amount = sum(abs(t["qty_change"]) * (t.get("price") or t.get("price_est") or 0) for t in sellers)
        if pct < min_pct and amount < min_amount:
            continue
        if not all(_is_corp(b) for b in buyers):
            sy = [_birth_year(t) for t in sellers]
            by = [_birth_year(t) for t in buyers]
            if None not in sy + by and min(by) <= max(sy):
                continue  # 개인끼리인데 받는 쪽이 아랫세대가 아님
        rep = sellers[0]
        rep.update(signal="gift", transfer=True,
                   giver_names=", ".join(t.get("person") or "" for t in sellers),
                   recipients=[dict(person=b.get("person"), qty=b["qty_change"],
                                    relation=b.get("relation"), pct_after=b.get("pct_after")) for b in buyers],
                   transfer_qty=qty, transfer_pct=round(pct, 6), transfer_amount=amount or None)
        rep["why"] = "그룹 내 이전(증여 화면)"
        n += 1
    return n


def pair_gifts(txs):
    outs = [t for t in txs if t.get("event") == "GIFT_OUT" and t["view"] != "dup"]
    ins = [t for t in txs if t.get("event") == "GIFT_IN" and t["view"] != "dup"]
    """증여 쪽 행을 기준으로 남기고, 짝이 맞는 수증 행은 dup으로 돌리며 수증자 정보를 붙인다.
    짝 찾기: 같은 종목·같은 날, 수증 수량 = 증여 수량(1명) 또는 수증 여러 명의 합."""
    used = set()

    def absorb(o, recv):
        o["recipients"] = [dict(person=i.get("person"), qty=i.get("qty_change"),
                                relation=i.get("relation"), pct_after=i.get("pct_after"))
                           for i in recv]
        o["counterparty"] = ", ".join(filter(None, (i.get("person") for i in recv)))
        for i in recv:
            used.add(i["id"])
            i["view"], i["why"], i["dup_of"] = "dup", "수증(증여 쪽 행에 합침)", o["id"]
            i["counterparty"] = o.get("person")

    for o in outs:
        cands = [i for i in ins if i["id"] not in used
                 and i.get("stock_code") == o.get("stock_code") and i.get("date") == o.get("date")]
        q = -(o.get("qty_change") or 0)
        one = next((i for i in cands if i.get("qty_change") == q), None)
        if one:
            absorb(o, [one])
        elif cands and sum(i.get("qty_change") or 0 for i in cands) == q:
            absorb(o, cands)
    for t in outs + ins:
        if not t.get("counterparty") and t.get("note"):
            m = re.search(r"(수증자|증여자)\s*[:：]\s*([^,/]+)", t["note"])
            if m:
                t["counterparty"] = m.group(2).strip()


def estimate_amounts(txs, book):
    """단가가 없는 내부자·계획 행에 변동일(이전 최근 거래일) 종가로 규모를 추정한다."""
    n = 0
    for t in txs:
        if t.get("view") != "insider" or t.get("price") or not t.get("qty_change"):
            continue
        hit = book.close_on(t.get("stock_code"), t.get("date") or t.get("rcept_dt"))
        if hit:
            close, d, _ = hit
            t["price_est"], t["price_date"] = close, d
            t["amount"] = abs(t["qty_change"]) * close
            t["amount_est"] = True
            n += 1
    return n


def build_feed(root: Path, days: int = 120, end: dt.date = None, log=print,
               detail_days: int = 30, price_db: Path = None):
    end = end or dt.date.today()
    start = end - dt.timedelta(days=days)
    loaded = load_days(root, start, end)
    filings, txs, plans, context = [], [], [], []
    for d in loaded:
        filings += d["filings"]
        txs += d["txs"]
        plans += d.get("plans", [])
        context += d.get("context", [])
    # 코넥스는 보지 않음(예전에 받아 둔 날짜 파일에 섞여 있어도 여기서 뺀다)
    filings = [f for f in filings if f.get("market") != "코넥스"]
    txs = [t for t in txs if t.get("market") != "코넥스"]
    plans = [p for p in plans if p.get("market") != "코넥스"]
    mark_superseded(filings, txs)
    ndup = dedup(txs)
    pair_gifts(txs)
    n_intra = mark_intra_group(txs)
    log(f"그룹 내 이동으로 묶어 매매 신호에서 뺀 행: {n_intra}")
    sectors = {}
    if price_db is not None:
        from .prices import load_sectors
        sectors = load_sectors(price_db, root, log=log)

    def put_sector(r):
        sec = sectors.get(r.get("stock_code") or "")
        if sec:
            r["sector_l"], r["sector_m"] = sec
        return r
    for t in txs:
        put_sector(t)
    for p in plans:
        put_sector(p)
    if price_db is not None:
        from .prices import PriceBook
        need = [t for t in txs if t.get("view") == "insider" and not t.get("price")]
        if need:
            book = PriceBook.load(price_db, [t.get("stock_code") for t in need],
                                  min(t.get("date") or t["rcept_dt"] for t in need),
                                  end.isoformat(), log=log)
            if book:
                log(f"단가 없는 {len(need)}행 중 {estimate_amounts(txs, book)}행을 종가로 추정")
    log(f"그룹 내 이전으로 증여 화면에 올린 건: {mark_transfers(txs)}")

    prices = {}
    if price_db is not None:  # 기업 페이지 차트용 종가
        from .prices import load_company_prices
        codes = {t.get("stock_code") for t in txs if t.get("view") == "insider"}
        codes |= {p.get("stock_code") for p in plans}
        codes |= {f.get("stock_code") for f in filings if str(f.get("form", "")).startswith("BULK")}  # 대량보유만 있는 종목
        prices = load_company_prices(price_db, codes, (start - dt.timedelta(days=730)).isoformat(), log=log)

    from .export import review_log, write_outputs
    write_outputs(root, start, end, filings, txs, plans, sectors, prices, log=log)

    sig = {t.get("stock_code") for t in txs if t.get("signal")}
    if sectors:
        miss = sorted({(t.get("stock_code"), t["corp_name"]) for t in txs
                       if t.get("signal") and t.get("stock_code") not in sectors})
        log(f"산업분류: 신호 종목 {len(sig)}개 중 {len(sig) - len(miss)}개 분류, 미분류 {len(miss)}개"
            + (f" (sector_overrides.csv로 채울 수 있음: {', '.join(n for _, n in miss[:8])}"
               f"{' 외' if len(miss) > 8 else ''})" if miss else ""))
    errors = [e for d in loaded for e in d.get("errors", [])]
    log(f"{len(loaded)}일 처리 · 중복 판정 {ndup} · 수집 오류 {len(errors)}")
    review_log(txs, end, log=log)
