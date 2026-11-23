"""하루치 수집: 공시 목록 → 원문 → 파싱 → 1·2층 분류 → data/days/YYYY/YYYYMMDD.json"""
import datetime as dt
import gzip
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .classify import EXTERNAL_NAME, classify_tx, is_external_filer
from .dart import Dart, DartError, LimitExceeded
from .parsers import FORM_LABEL, form_of, parse

MARKET = {"Y": "코스피", "K": "코스닥", "N": "코넥스", "E": "기타"}
# 나중에(4층 태그) 쓰려고 목록만 남겨 두는 공시
CONTEXT_KEYWORDS = ("공개매수", "의결권대리행사")


def dart_url(rcept_no):
    return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"


def _id(v):
    return re.sub(r"\D", "", v or "")


class Store:
    def __init__(self, root: Path, keep_raw: bool = True):
        self.root = root
        self.keep_raw = keep_raw

    def raw_path(self, rcept_no):
        return self.root / "data" / "raw" / rcept_no[:4] / f"{rcept_no}.xml"

    def day_path(self, date):
        return self.root / "data" / "days" / date[:4] / f"{date}.json.gz"

    def get_raw(self, rcept_no):
        p = self.raw_path(rcept_no)
        return p.read_bytes() if p.exists() else None

    def put_raw(self, rcept_no, raw):
        if self.keep_raw:
            p = self.raw_path(rcept_no)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(raw)

    def write_day(self, date, obj):
        p = self.day_path(date)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        p.write_bytes(gzip.compress(data, mtime=0))  # mtime=0: 내용이 같으면 파일도 같게
        return p

    def read_day(self, date):
        p = self.day_path(date)
        return json.loads(gzip.decompress(p.read_bytes())) if p.exists() else None


def build_filing(row: dict, form: str, parsed: dict) -> tuple:
    """목록 행 + 파싱 결과 → (filing 메타, 거래 행 리스트, 계획 행 리스트)."""
    external, ext_why = is_external_filer(form, parsed, row.get("corp_name", ""))
    filing = dict(
        rcept_no=row["rcept_no"],
        rcept_dt=f"{row['rcept_dt'][:4]}-{row['rcept_dt'][4:6]}-{row['rcept_dt'][6:]}",
        form=form, form_label=FORM_LABEL[form], report_nm=row["report_nm"],
        is_amend=row["report_nm"].startswith("["),
        corrected_later="정" in (row.get("rm") or ""),
        corp_code=row["corp_code"], corp_name=row["corp_name"],
        stock_code=row.get("stock_code") or None,
        market=MARKET.get(row.get("corp_cls"), row.get("corp_cls")),
        filer=row.get("flr_nm"),
        reporter=parsed.get("reporter"), purpose=parsed.get("purpose"),
        shares=parsed.get("shares"),
        group_pct_before=parsed.get("group_pct_before"),
        group_pct_after=parsed.get("group_pct_after"),
        change_method=parsed.get("change_method"),
        reason_text=parsed.get("reason_text"),
        funding=parsed.get("funding") or [],
        external=external, external_why=ext_why,
        url=dart_url(row["rcept_no"]),
    )
    fund = {}
    for f in filing["funding"]:
        fund[_id(f.get("person_id")) or f.get("person")] = f
        fund.setdefault(f.get("person"), f)

    common = {k: filing[k] for k in (
        "rcept_no", "rcept_dt", "form", "form_label", "is_amend", "corp_code",
        "corp_name", "stock_code", "market", "filer", "purpose", "shares", "url")}
    txs = []
    for i, t in enumerate(parsed.get("txs") or []):
        t = dict(t)
        if t.get("person") and re.fullmatch(r"(?:[가-힣] )+[가-힣]", t["person"]):
            t["person"] = t["person"].replace(" ", "")  # '홍 길 동' → '홍길동'
        ext, why = external, ext_why
        if form == "MAJOR" and EXTERNAL_NAME.search(t.get("person") or ""):
            ext, why = True, "기관·외부 주주"
        classify_tx(t, form, ext, why)
        f = fund.get(_id(t.get("person_id"))) or fund.get(t.get("person"))
        if f and t["direction"] == "buy":
            t["funding"] = {k: f.get(k) for k in ("own", "loan", "other", "detail")}
        txs.append(dict(id=f"{row['rcept_no']}-{i}", **common, **t))

    plans = []
    for i, p in enumerate(parsed.get("plans") or []):
        sign = 1 if "(+)" in (p.get("method") or "") else -1 if "(-)" in (p.get("method") or "") else 0
        plans.append(dict(
            id=f"{row['rcept_no']}-p{i}", **common, **p, sign=sign,
            person=(parsed.get("reporter") or {}).get("name"),
            relation=(parsed.get("reporter") or {}).get("relation"),
            position=(parsed.get("reporter") or {}).get("position"),
            purpose_text=parsed.get("purpose"), plan_ratio=parsed.get("plan_ratio"),
            trade_start=parsed.get("trade_start"),
        ))
    filing["n_tx"] = len(txs)
    filing["n_plan"] = len(plans)
    return filing, txs, plans


def collect_day(dart: Dart, store: Store, date: str, log=print, workers: int = 4) -> dict:
    """date: 'YYYYMMDD'. 하루치를 모아 저장하고 요약 dict를 돌려준다."""
    rows_d = dart.list_filings(date, "D")
    rows_i = [r for r in dart.list_filings(date, "I") if form_of(r["report_nm"]) == "MAJOR"]
    targets, context = [], []
    for r in rows_d + rows_i:
        form = form_of(r["report_nm"])
        if form and r.get("corp_cls") in ("Y", "K"):  # 코넥스(N) 제외
            targets.append((r, form))
        elif any(k in r["report_nm"] for k in CONTEXT_KEYWORDS):
            context.append({k: r.get(k) for k in (
                "rcept_no", "rcept_dt", "corp_code", "corp_name", "stock_code",
                "report_nm", "flr_nm")})

    # 원문은 몇 개씩 동시에 받는다(한 건에 1~2초 걸림)
    need = [r["rcept_no"] for r, _ in targets if store.get_raw(r["rcept_no"]) is None]
    fetched, fetch_err = {}, {}

    def _fetch(no):
        try:
            return no, dart.document(no), None
        except Exception as e:  # LimitExceeded 포함, 아래에서 처리
            return no, None, e

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for no, raw, err in ex.map(_fetch, need):
            if raw is not None:
                store.put_raw(no, raw)
                fetched[no] = raw
            else:
                fetch_err[no] = err
    if any(isinstance(e, LimitExceeded) for e in fetch_err.values()):
        raise LimitExceeded("020", "사용한도 초과")

    filings, txs, plans, errors = [], [], [], []
    for r, form in targets:
        raw = fetched.get(r["rcept_no"]) or store.get_raw(r["rcept_no"])
        try:
            if raw is None:
                raise fetch_err.get(r["rcept_no"]) or DartError("?", "원문 없음")
            parsed = parse(form, raw)
            f, t, p = build_filing(r, form, parsed)
            filings.append(f)
            txs += t
            plans += p
        except Exception as e:  # 한 건 실패로 하루 전체를 버리지 않는다
            errors.append(dict(rcept_no=r["rcept_no"], report_nm=r["report_nm"],
                               corp_name=r["corp_name"], error=repr(e)[:300]))
    counts = {}
    for t in txs:
        counts[t["view"]] = counts.get(t["view"], 0) + 1
    obj = dict(
        date=f"{date[:4]}-{date[4:6]}-{date[6:]}",
        collected_at=dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec="seconds"),
        n_list_d=len(rows_d), n_targets=len(targets), counts=counts,
        filings=filings, txs=txs, plans=plans, context=context, errors=errors,
    )
    store.write_day(date, obj)
    log(f"{date}: 대상 {len(targets)}건, 거래행 {len(txs)} "
        f"(내부자 {counts.get('insider', 0)} / 외부 {counts.get('external', 0)} / "
        f"노이즈 {counts.get('noise', 0)}), 계획 {len(plans)}, 오류 {len(errors)}")
    return obj
