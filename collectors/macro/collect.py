"""매크로 데이터 수집기 (표준 라이브러리만 사용).

수집 대상
  FRED  : fredgraph.csv (키 불필요)            -> raw/fred.json
  ALFRED: 발표일 탐지 + 직전 빈티지(수정치 계산)  -> raw/alfred.json
  ECOS  : 한국은행 (ECOS_API_KEY 없으면 sample) -> raw/ecos.json
  FF    : ForexFactory 이번 주 캘린더(컨센서스)  -> raw/ff_thisweek.json + consensus_archive.json 누적
  Fed   : FOMC 성명 HTML (최근 2회)             -> raw/fomc.json
  Kalshi: KXFED 정책금리 시장                    -> raw/kalshi.json
  수출  : 레포 dashboard-data.json 재사용        -> raw/exports.json

사용 (레포 루트): python collectors/macro/collect.py --out data/macro --exports dashboard/data/dashboard-data.json
                 python collectors/macro/build.py --out data/macro --dest dashboard/macro/data
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import datetime as dt
import html
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C  # noqa: E402

# FRED 는 브라우저형/임의 User-Agent 를 차단(연결 종료)하고 기본 UA(Python-urllib, curl)만 허용한다.
UA_OVERRIDE: dict[str, str] = {}
_curl_hosts: set[str] = set(h for h in os.environ.get("MACRO_CURL_HOSTS", "").split(",") if h)
LOG: list[str] = []


def log(msg: str) -> None:
    LOG.append(msg)
    print(msg, flush=True)


# ─────────────────────────── HTTP (urllib → curl 폴백)
def http_get(url: str, timeout: int = 40, retries: int = 2) -> bytes:
    host = urllib.parse.urlparse(url).netloc
    last = None
    for attempt in range(retries + 1):
        if host not in _curl_hosts:
            try:
                hdr = {"User-Agent": UA_OVERRIDE[host]} if host in UA_OVERRIDE else {}
                req = urllib.request.Request(url, headers=hdr)
                with urllib.request.urlopen(req, timeout=min(timeout, 25)) as r:
                    return r.read()
            except Exception as e:  # 일부 환경에서 FRED 가 urllib 연결을 끊음 → curl 로 전환
                last = e
                _curl_hosts.add(host)
        try:
            out = subprocess.run(["curl", "-sS", "-L", "--fail", "--http1.1", "--max-time", str(timeout), url], capture_output=True, timeout=timeout + 10)
            if out.returncode == 0 and out.stdout:
                return out.stdout
            last = RuntimeError(out.stderr.decode(errors="ignore")[:200] or f"curl rc={out.returncode}")
        except Exception as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET 실패 {url}: {last}")


def parse_two_col_csv(raw: bytes) -> list[list]:
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8", errors="ignore"))))
    if not rows or not rows[0] or "date" not in rows[0][0].lower():
        raise ValueError("CSV 형식 아님: " + raw[:80].decode(errors="ignore"))
    obs = []
    for r in rows[1:]:
        if len(r) < 2:
            continue
        v = r[1].strip()
        obs.append([r[0], None if v in ("", ".") else float(v)])
    return obs


# ─────────────────────────── FRED / ALFRED
def fred_series(sid: str, start: str) -> list[list]:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}"
    return parse_two_col_csv(http_get(url))


def alfred_vintage(sid: str, vintage: str, start: str = "2024-01-01") -> list[list]:
    url = (f"https://alfred.stlouisfed.org/graph/alfredgraph.csv?id={sid}"
           f"&vintage_date={vintage}&cosd={start}")
    return parse_two_col_csv(http_get(url))


def _has_obs(obs: list[list], date: str) -> bool:
    return any(d == date and v is not None for d, v in obs)


def find_release_date(sid: str, obs_date: str, today: dt.date) -> str | None:
    """obs_date 관측치가 처음 등장한 빈티지(=발표일)를 이분 탐색."""
    lo = dt.date.fromisoformat(obs_date)
    lo = lo + dt.timedelta(days=1)
    hi = today
    try:
        if not _has_obs(alfred_vintage(sid, hi.isoformat()), obs_date):
            return None
    except Exception as e:
        log(f"  ALFRED {sid} 실패: {e}")
        return None
    while lo < hi:
        mid = lo + (hi - lo) // 2
        if _has_obs(alfred_vintage(sid, mid.isoformat()), obs_date):
            hi = mid
        else:
            lo = mid + dt.timedelta(days=1)
    return hi.isoformat()


def fred_batch(ids: list[str]) -> dict:
    """여러 시리즈를 한 번에 요청. 주기가 섞이면 FRED 가 주기별 CSV 를 ZIP 으로 돌려준다."""
    starts = ",".join(C.FRED[i][2] for i in ids)
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={','.join(ids)}&cosd={starts}"
    raw = http_get(url, timeout=90)
    tables = []
    if raw[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(raw))
        tables = [z.read(n) for n in z.namelist() if n.endswith(".csv")]
    else:
        tables = [raw]
    out = {}
    for t in tables:
        rows = list(csv.reader(io.StringIO(t.decode("utf-8", errors="ignore"))))
        if not rows or "date" not in rows[0][0].lower():
            continue
        head = rows[0]
        for j, sid in enumerate(head[1:], start=1):
            obs = []
            for r in rows[1:]:
                if len(r) > j and r[j].strip() not in ("", "."):
                    obs.append([r[0], float(r[j])])
            out[sid] = obs
    return out


def collect_fred(out: Path) -> dict:
    """시리즈 20개씩 묶음 요청 (단건 요청 대비 수십 배 빠름). 묶음 실패 시 단건으로 재시도."""
    ids = list(C.FRED)
    res, fails = {}, []
    for i in range(0, len(ids), 20):
        chunk = ids[i:i + 20]
        try:
            got = fred_batch(chunk)
        except Exception as e:
            log(f"  FRED 묶음 실패({chunk[0]}..): {e} → 단건 재시도")
            got = {}
        for sid in chunk:
            obs = got.get(sid)
            if obs is None:
                try:
                    obs = [o for o in fred_series(sid, C.FRED[sid][2]) if o[1] is not None]
                except Exception as e:
                    fails.append(sid)
                    log(f"  FRED 실패 {sid}: {e}")
                    continue
            name, unit, _ = C.FRED[sid]
            res[sid] = {"name": name, "unit": unit, "source": "FRED", "obs": obs}
    log(f"FRED {len(res)}/{len(ids)} 수집 (실패 {fails})")
    return res


def collect_alfred(fred: dict, today: dt.date) -> dict:
    """주요 발표의 발표일과 직전 빈티지(수정 전 값)를 수집."""
    out = {}
    targets = {"PAYEMS": "US_NFP", "CPIAUCSL": "US_CPI", "RSAFS": "US_RETAIL", "ICSA": "US_CLAIMS"}
    for sid, key in targets.items():
        obs = [o for o in fred.get(sid, {}).get("obs", []) if o[1] is not None]
        if not obs:
            continue
        last = obs[-1][0]
        rel = find_release_date(sid, last, today)
        rec = {"series": sid, "period": last, "release": rel}
        if rel and sid in ("PAYEMS", "RSAFS"):
            prev_vintage = (dt.date.fromisoformat(rel) - dt.timedelta(days=1)).isoformat()
            try:
                rec["prevVintage"] = prev_vintage
                rec["prevObs"] = [o for o in alfred_vintage(sid, prev_vintage) if o[1] is not None][-8:]
            except Exception as e:
                log(f"  ALFRED 빈티지 실패 {sid}: {e}")
        out[key] = rec
        log(f"ALFRED {key}: {last} 발표 {rel}")
    return out


# ─────────────────────────── ECOS
def ecos_rows(stat, cycle, start, end, item1, item2=None):
    key = os.environ.get("ECOS_API_KEY", "").strip() or "sample"
    n = 10 if key == "sample" else 1000
    path = f"{stat}/{cycle}/{start}/{end}/{item1}" + (f"/{item2}" if item2 else "")
    url = f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/{n}/{path}"
    d = json.loads(http_get(url, timeout=30))
    v = d.get("StatisticSearch")
    if not v:
        msg = d.get("RESULT", {}).get("MESSAGE", "")
        if "데이터가 없습니다" in msg or "해당하는 데이터" in msg:
            return []
        raise RuntimeError(str(d)[:200])
    return v.get("row", [])


def _windows(cycle: str, today: dt.date, sample: bool, per_row: int):
    """sample 키(10행)일 때 기간을 10행 이하 창으로 쪼갠다."""
    if cycle == "D":
        start = today - dt.timedelta(days=C.ECOS_DAILY_DAYS)
        step = dt.timedelta(days=13 if sample else 400)
        cur = start
        while cur <= today:
            end = min(cur + step, today)
            yield cur.strftime("%Y%m%d"), end.strftime("%Y%m%d")
            cur = end + dt.timedelta(days=1)
    else:
        months = []
        y, m = today.year, today.month
        for _ in range(C.ECOS_MONTHS):
            months.append(f"{y}{m:02d}")
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        months.reverse()
        size = (10 // per_row) if sample else 1000
        for i in range(0, len(months), size):
            chunk = months[i:i + size]
            yield chunk[0], chunk[-1]


def collect_ecos(today: dt.date) -> dict:
    sample = not os.environ.get("ECOS_API_KEY", "").strip()
    res = {}
    def job(key):
        stat, cycle, i1, i2, name, unit, filt = C.ECOS[key]
        per_row = 2 if filt else 1
        obs = {}
        for s, e in _windows(cycle, today, sample, per_row):
            for r in ecos_rows(stat, cycle, s, e, i1, i2):
                if filt and r.get("ITEM_NAME2") != filt:
                    continue
                t = r["TIME"]
                d = f"{t[:4]}-{t[4:6]}-{t[6:8] if len(t) == 8 else '01'}"
                try:
                    obs[d] = float(r["DATA_VALUE"])
                except (TypeError, ValueError):
                    pass
        return key, sorted(obs.items())
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        for f in cf.as_completed([ex.submit(job, k) for k in C.ECOS]):
            try:
                key, obs = f.result()
                stat, cycle, i1, i2, name, unit, _ = C.ECOS[key]
                res[key] = {"name": name, "unit": unit, "source": "ECOS",
                            "stat": f"{stat}/{i1}", "obs": [list(o) for o in obs]}
            except Exception as e:
                log(f"  ECOS 실패: {e}")
    log(f"ECOS {len(res)}/{len(C.ECOS)} 수집 ({'sample 키' if sample else 'API 키'})")
    return res


# ─────────────────────────── ForexFactory 컨센서스 (주간 스냅샷 누적)
def _num(s):
    if s in (None, ""):
        return None
    s = s.strip().replace(",", "")
    mult = 1.0
    if s.endswith("%"):
        s = s[:-1]
    elif s[-1:] in "KMB":
        mult = {"K": 1e3, "M": 1e6, "B": 1e9}[s[-1]]
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None


def collect_ff(out: Path) -> list:
    try:
        events = json.loads(http_get("https://nfs.faireconomy.media/ff_calendar_thisweek.json"))
    except Exception as e:
        log(f"FF 실패: {e}")
        return []
    arc_path = out / "consensus_archive.json"
    arc = json.loads(arc_path.read_text()) if arc_path.exists() else {}
    keep = []
    for e in events:
        if e.get("country") not in ("USD", "KRW", "CNY", "EUR", "JPY"):
            continue
        rec = {"date": e["date"], "country": e["country"], "impact": e.get("impact"),
               "title": e["title"], "forecast": e.get("forecast") or None,
               "previous": e.get("previous") or None}
        keep.append(rec)
        key = C.FF_MAP.get(e["title"]) if e["country"] == "USD" else None
        if key and rec["forecast"]:
            arc[f"{e['date'][:10]}|{key}"] = {"forecast": _num(rec["forecast"]), "raw": rec["forecast"],
                                              "previous": rec["previous"], "source": "ForexFactory",
                                              "captured": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    arc_path.write_text(json.dumps(arc, ensure_ascii=False, indent=1, sort_keys=True))
    log(f"FF 이번 주 {len(keep)}건, 컨센서스 아카이브 {len(arc)}건")
    return keep


# 발표 후 실제치(첫 발표값)를 컨센서스 아카이브에 기록 → 서프라이즈 이력이 ForexFactory 만으로 이어진다
#   키: (FRED 시리즈, 계산) — 기준월 = 발표월의 전월 (CPI·고용·소매·PPI·PCE)
ACTUAL_RULES = {
    "US_CPI.headline_mom": ("CPIAUCSL", "mom"), "US_CPI.core_mom": ("CPILFESL", "mom"),
    "US_CPI.headline_yoy": ("CPIAUCSL", "yoy"),
    "US_NFP.nfp": ("PAYEMS", "diff_k"), "US_NFP.ur": ("UNRATE", "level"), "US_NFP.ahe_mom": ("CES0500000003", "mom"),
    "US_RETAIL.headline_mom": ("RSAFS", "mom"), "US_RETAIL.exauto_mom": ("RSFSXMV", "mom"),
    "US_PPI.headline_mom": ("PPIFIS", "mom"), "US_PPI.core_mom": ("PPIFES", "mom"),
    "US_PCE.core_mom": ("PCEPILFE", "mom"), "US_CLAIMS.icsa": ("ICSA", "week"),
}


def _prev_month(d: str, n: int = 1) -> str:
    y, m = int(d[:4]), int(d[5:7]) - n
    while m <= 0:
        y, m = y - 1, m + 12
    return f"{y}-{m:02d}-01"


def fill_actuals(out: Path, fred: dict, today: dt.date) -> None:
    arc_path = out / "consensus_archive.json"
    if not arc_path.exists():
        return
    arc = json.loads(arc_path.read_text())
    filled = 0
    for k, v in arc.items():
        rel, key = k.split("|", 1)
        if v.get("actual") is not None or key not in ACTUAL_RULES or rel > today.isoformat():
            continue
        sid, how = ACTUAL_RULES[key]
        mp = {d: x for d, x in fred.get(sid, {}).get("obs", [])}
        val = None
        if how == "week":  # 발표 목요일 기준 직전 토요일 주간
            wk = (dt.date.fromisoformat(rel) - dt.timedelta(days=5)).isoformat()
            val = mp.get(wk)
        else:
            ref = _prev_month(rel)
            if how == "level":
                val = mp.get(ref)
            elif how == "mom" and ref in mp and _prev_month(ref) in mp:
                val = round((mp[ref] / mp[_prev_month(ref)] - 1) * 100, 1)
            elif how == "yoy" and ref in mp and _prev_month(ref, 12) in mp:
                val = round((mp[ref] / mp[_prev_month(ref, 12)] - 1) * 100, 1)
            elif how == "diff_k" and ref in mp and _prev_month(ref) in mp:
                val = (mp[ref] - mp[_prev_month(ref)]) * 1000  # FF 단위(명)와 맞춤
        if val is not None:
            v["actual"] = val
            v["actualCaptured"] = today.isoformat()
            filled += 1
    arc_path.write_text(json.dumps(arc, ensure_ascii=False, indent=1, sort_keys=True))
    log(f"컨센서스 아카이브 실제치 기록 {filled}건")


# ─────────────────────────── FOMC 성명
def _statement_text(raw: str) -> dict:
    i = raw.find('id="article"')
    seg = raw[i:i + 20000] if i > 0 else raw
    paras = [html.unescape(re.sub(r"<[^>]+>", "", p)) for p in re.findall(r"<p[^>]*>(.*?)</p>", seg, re.S)]
    paras = [re.sub(r"\s+", " ", p).strip() for p in paras]
    body = []
    for p in paras:
        if not p or p.startswith("For release") or re.match(r"^[A-Z][a-z]+ \d{1,2}, \d{4}$", p):
            continue
        if p.startswith("For media inquiries") or p.startswith("Implementation Note"):
            break
        body.append(p)
    vote = re.search(r"by a (\d+)\s*(?:&#8211;|–|-)\s*(\d+) vote", raw)
    against = next((p for p in body if p.startswith("Voting against")), None)
    body = [p for p in body if not p.startswith("Voting against") and "approved the following statement" not in p]
    return {"paragraphs": body, "vote": [int(vote.group(1)), int(vote.group(2))] if vote else None,
            "dissent": against}


def collect_fomc(today: dt.date) -> list:
    past = [d for d in C.FOMC_DATES if d <= today.isoformat()][-2:]
    res = []
    for d in past:
        url = f"https://www.federalreserve.gov/newsevents/pressreleases/monetary{d.replace('-', '')}a.htm"
        try:
            st = _statement_text(http_get(url).decode("utf-8", errors="ignore"))
            res.append({"date": d, "url": url, **st})
        except Exception as e:
            log(f"FOMC {d} 실패: {e}")
    nxt = [d for d in C.FOMC_DATES if d > today.isoformat()]
    log(f"FOMC 성명 {len(res)}건 (다음 회의 {nxt[:1]})")
    return res


# ─────────────────────────── Kalshi 정책금리 시장
def collect_kalshi() -> list:
    base = "https://api.elections.kalshi.com/trade-api/v2"
    try:
        evs = json.loads(http_get(f"{base}/events?series_ticker=KXFED&status=open&limit=6"))["events"]
    except Exception as e:
        log(f"Kalshi 실패: {e}")
        return []
    out = []
    for ev in sorted(evs, key=lambda e: e["event_ticker"])[:3]:
        try:
            ms = json.loads(http_get(f"{base}/markets?event_ticker={ev['event_ticker']}&limit=60"))["markets"]
        except Exception as e:
            log(f"  Kalshi {ev['event_ticker']} 실패: {e}")
            continue
        strikes = []
        for m in ms:
            k = m.get("floor_strike")
            bid, ask = float(m.get("yes_bid_dollars") or 0), float(m.get("yes_ask_dollars") or 0)
            last = float(m.get("last_price_dollars") or 0)
            p = (bid + ask) / 2 if ask > 0 and ask - bid <= 0.1 else last
            if k is not None:
                strikes.append({"strike": float(k), "pAbove": round(p, 4)})
        out.append({"event": ev["event_ticker"], "title": ev.get("title"), "sub": ev.get("sub_title"),
                    "strikes": sorted(strikes, key=lambda s: s["strike"])})
    log(f"Kalshi 이벤트 {len(out)}건")
    return out


# ─────────────────────────── 한국 수출 (기존 수출 대시보드 데이터 재사용)
def collect_exports(path: Path | None) -> dict:
    if not path or not path.exists():
        log("수출 데이터 경로 없음 → 생략")
        return {}
    d = json.loads(path.read_text())
    cols = d["columns"]["summary"]
    summ = [dict(zip(cols, r)) for r in d["summary"]][-40:]
    icol = d["columns"]["industries"]
    months = sorted({r[0] for r in d["industries"]})[-40:]
    inds = [dict(zip(icol, r)) for r in d["industries"] if r[0] in months]
    log(f"수출 데이터 asOf {d.get('asOf')} ({d.get('status')})")
    return {"asOf": d.get("asOf"), "status": d.get("status"), "updatedAt": d.get("updatedAt"),
            "sourceUrl": d.get("sourceReleaseUrl"), "summary": summ, "industries": inds}


# ─────────────────────────── Investing.com 컨센서스 (브라우저 추출 파일 병합)
def collect_investing(src: str, out: Path) -> dict:
    """investing_extract.js 가 받은 JSON 을 raw/investing.json 에 병합.
    src = 파일 경로 또는 폴더(가장 최근 investing_consensus_*.json 사용)."""
    p = Path(src)
    if p.is_dir():
        cands = sorted(p.glob("investing_consensus_*.json"), key=lambda x: x.stat().st_mtime)
        if not cands:
            log(f"Investing: {p} 에 investing_consensus_*.json 없음")
            return {}
        p = cands[-1]
    new = json.loads(p.read_text(encoding="utf-8"))
    dst = out / "raw" / "investing.json"
    old = json.loads(dst.read_text()) if dst.exists() else {"events": {}}
    ok = {k: v for k, v in new.get("events", {}).items() if v and not v.get("error")}
    bad = [k for k, v in new.get("events", {}).items() if not v or v.get("error")]
    for k, v in ok.items():
        prev = old["events"].get(k, {})
        # 과거 발표의 예측치는 새 응답에서 빠질 수 있으므로 occurrence 단위로 합친다
        occ = {o["occurrence_time"]: o for o in prev.get("occurrences", [])}
        occ.update({o["occurrence_time"]: o for o in v.get("occurrences", [])})
        v["occurrences"] = sorted(occ.values(), key=lambda o: o["occurrence_time"], reverse=True)[:120]
        v["fetchedAt"] = new.get("fetchedAt")
        old["events"][k] = v
    old["updatedAt"] = new.get("fetchedAt")
    old["file"] = p.name
    dst.write_text(json.dumps(old, ensure_ascii=False))
    log(f"Investing {p.name}: 이벤트 {len(ok)}개 병합 (실패 {bad})")
    return old


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/macro")
    ap.add_argument("--exports", default="")
    ap.add_argument("--today", default="")
    ap.add_argument("--investing", default="", help="investing_extract.js 결과 파일 또는 폴더")
    ap.add_argument("--only", default="", help="쉼표 구분: fred,alfred,ecos,ff,fomc,kalshi,exports,investing")
    a = ap.parse_args(argv)
    out = Path(a.out)
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    only = set(a.only.split(",")) if a.only else None
    def want(k): return only is None or k in only
    t0 = time.time()
    if want("fred"):
        fred = collect_fred(out)
        (raw / "fred.json").write_text(json.dumps(fred, ensure_ascii=False))
    else:
        fred = json.loads((raw / "fred.json").read_text())
    if want("alfred"):
        (raw / "alfred.json").write_text(json.dumps(collect_alfred(fred, today), ensure_ascii=False, indent=1))
    if want("ecos"):
        (raw / "ecos.json").write_text(json.dumps(collect_ecos(today), ensure_ascii=False))
    if want("ff"):
        (raw / "ff_thisweek.json").write_text(json.dumps(collect_ff(out), ensure_ascii=False, indent=1))
        fill_actuals(out, fred, today)
    if want("fomc"):
        (raw / "fomc.json").write_text(json.dumps(collect_fomc(today), ensure_ascii=False, indent=1))
    if want("kalshi"):
        (raw / "kalshi.json").write_text(json.dumps(collect_kalshi(), ensure_ascii=False, indent=1))
    if want("investing") and a.investing:
        collect_investing(a.investing, out)
    if want("exports"):
        (raw / "exports.json").write_text(json.dumps(collect_exports(Path(a.exports) if a.exports else None), ensure_ascii=False))
    (raw / "collect_log.json").write_text(json.dumps({"ranAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                                     "seconds": round(time.time() - t0, 1), "log": LOG}, ensure_ascii=False, indent=1))
    log(f"완료 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
