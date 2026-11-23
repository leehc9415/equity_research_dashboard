"""단가가 없는 거래의 규모 추정용 종가 조회.

종가 출처는 둘 중 하나다.
- stockDB의 DuckDB 파일(prices_daily: symbol_id=6자리 코드, date, close, mcap) — PC에서 돌 때
- 증시데이터 종목차트 폴더(charts/index.js + charts/<코드>.js) — GitHub Actions에서 돌 때.
  market-charts 릴리스의 charts.zip을 푼 폴더를 --price-db로 넘기면 된다.
출처가 없거나 열 수 없으면(다른 프로그램이 쓰는 중 등) 추정 없이 넘어간다.
"""
import json
import bisect
import datetime as dt
import os
from pathlib import Path

MAX_GAP_DAYS = 7  # 변동일 이전 7일 안에 종가가 없으면 추정하지 않음


def default_db_path(root: Path) -> Path:
    env = os.environ.get("STOCKDB_DB")
    if env:
        return Path(env)
    return root.parent / "data" / "market.duckdb"  # stockDB/insider_tracker 기준


# ── 종목차트 폴더 읽기 ──
_IDX = {}


def _chart_index(folder: Path) -> dict:
    """코드 → {name, sector, sub, mcap(원), close}. 시총은 마지막 날 기준."""
    key = str(folder)
    if key not in _IDX:
        out = {}
        p = folder / "index.js"
        if p.exists():
            s = p.read_text(encoding="utf-8")
            idx = json.loads(s[s.index("=") + 1:].rstrip().rstrip(";"))
            cols = idx["cols"]
            for r in idx["rows"]:
                a = dict(zip(cols, r))
                out[a["code"]] = {"name": a.get("name"), "sector": a.get("sector"), "sub": a.get("sub"),
                                  "mcap": (a["mcap"] * 1e8) if a.get("mcap") else None, "close": a.get("close")}
        _IDX[key] = out
    return _IDX[key]


def _chart_series(folder: Path, code: str):
    """[(date, close), ...] 날짜 오름차순."""
    p = folder / f"{code}.js"
    if not p.exists():
        return []
    s = p.read_text(encoding="utf-8")
    o = json.loads(s[s.index("]=") + 2:].rstrip().rstrip(";"))
    out, d = [], None
    for i, v in enumerate(o["d"]):
        d = dt.date(v // 10000, v // 100 % 100, v % 100) if i == 0 else d + dt.timedelta(days=v)
        out.append((d, o["c"][i]))
    return out


def _chart_rows(folder: Path, codes, start: str, end: str):
    """PriceBook 행 (code, date, close, mcap). 시총은 마지막 시총의 주식수 × 그날 종가로 근사."""
    idx = _chart_index(folder)
    rows = []
    for code in codes:
        ser = [(d, c) for d, c in _chart_series(folder, code) if start <= d.isoformat() <= end and c]
        info = idx.get(code) or {}
        shares = info["mcap"] / info["close"] if info.get("mcap") and info.get("close") else None
        rows += [(code, d, c, shares * c if shares else None) for d, c in ser]
    return rows


class PriceBook:
    def __init__(self, rows):
        self.by_code = {}
        for code, d, close, mcap in rows:
            self.by_code.setdefault(code, []).append((d.isoformat(), close, mcap))
        for v in self.by_code.values():
            v.sort()
        self.last_date = max((v[-1][0] for v in self.by_code.values()), default=None)

    @classmethod
    def load(cls, db_path: Path, codes, start: str, end: str, log=print):
        codes = sorted({c for c in codes if c})
        if not codes:
            return None
        if not db_path.exists():
            log(f"종가 DB가 없어 금액 추정을 건너뜁니다: {db_path}")
            return None
        s = (dt.date.fromisoformat(start) - dt.timedelta(days=MAX_GAP_DAYS + 3)).isoformat()
        if db_path.is_dir():
            book = cls(_chart_rows(db_path, codes, s, end))
            log(f"종가(종목차트): {len(book.by_code)}개 종목, 마지막 날짜 {book.last_date}")
            return book
        try:
            import duckdb
        except ImportError:
            log("duckdb 모듈이 없어 금액 추정을 건너뜁니다 (pip install duckdb).")
            return None
        try:
            con = duckdb.connect(str(db_path), read_only=True)
            s = (dt.date.fromisoformat(start) - dt.timedelta(days=MAX_GAP_DAYS + 3)).isoformat()
            rows = con.execute(
                "SELECT symbol_id, date, close, mcap FROM prices_daily "
                "WHERE symbol_id IN (SELECT unnest(?)) AND date BETWEEN ? AND ? AND close > 0",
                [codes, s, end]).fetchall()
            con.close()
        except Exception as e:  # 잠금 등
            log(f"종가 DB를 열지 못해 금액 추정을 건너뜁니다: {e}")
            return None
        book = cls(rows)
        log(f"종가 DB: {len(book.by_code)}개 종목, 마지막 날짜 {book.last_date}")
        return book

    def close_on(self, code: str, date: str):
        """date 당일 또는 그 이전 가장 가까운 거래일의 (종가, 그 날짜, 시총)."""
        seq = self.by_code.get(code)
        if not seq or not date:
            return None
        i = bisect.bisect_right(seq, (date, float("inf"), float("inf"))) - 1
        if i < 0:
            return None
        d, close, mcap = seq[i]
        if (dt.date.fromisoformat(date) - dt.date.fromisoformat(d)).days > MAX_GAP_DAYS:
            return None
        return close, d, mcap


def load_sectors(db_path: Path, root: Path = None, log=print) -> dict:
    """종목코드 → (대분류, 중분류). stockDB의 이중분류(se_sector_map)를 쓰고,
    insider_tracker/sector_overrides.csv(code,대분류,중분류)가 있으면 덮어쓴다."""
    out = {}
    if db_path and db_path.is_dir():
        for code, a in _chart_index(db_path).items():
            if a.get("sector"):
                out[code] = (a["sector"], a.get("sub"))
    elif db_path and db_path.exists():
        try:
            import duckdb
            con = duckdb.connect(str(db_path), read_only=True)
            for code, l, m in con.execute("SELECT code, se_l, se_m FROM se_sector_map").fetchall():
                out[code] = (l, m)
            con.close()
        except Exception as e:
            log(f"이중분류를 읽지 못했습니다: {e}")
    if root:
        p = root / "sector_overrides.csv"
        if p.exists():
            import csv
            with p.open(encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    code = (r.get("code") or "").strip().zfill(6)
                    if code and r.get("대분류"):
                        out[code] = (r["대분류"].strip(), (r.get("중분류") or "").strip() or None)
    return out


def load_company_prices(db_path: Path, codes, start: str, log=print) -> dict:
    """종목별 일별 종가와 마지막 시총(기업 페이지 차트용). {코드: {"d": [...], "c": [...], "mcap": ...}}"""
    codes = sorted({c for c in codes if c})
    if not codes or not db_path or not db_path.exists():
        return {}
    if db_path.is_dir():
        by = {}
        for code, d, c, m in _chart_rows(db_path, codes, start, "9999-12-31"):
            a = by.setdefault(code, {"d": [], "c": [], "mcap": None})
            a["d"].append(d.isoformat())
            a["c"].append(c)
            a["mcap"] = m
        return by
    try:
        import duckdb
        con = duckdb.connect(str(db_path), read_only=True)
        rows = con.execute(
            "SELECT symbol_id, date, close, mcap FROM prices_daily "
            "WHERE symbol_id IN (SELECT unnest(?)) AND date >= ? AND close > 0 ORDER BY symbol_id, date",
            [codes, start]).fetchall()
        con.close()
    except Exception as e:
        log(f"종가를 읽지 못했습니다: {e}")
        return {}
    by = {}
    for code, d, c, m in rows:
        a = by.setdefault(code, {"d": [], "c": [], "mcap": None})
        a["d"].append(d.isoformat())
        a["c"].append(c)
        a["mcap"] = m
    return by
