"""DART 전자공시 원문(dart4.xsd XML)에서 코드가 붙은 표 셀을 뽑는 범용 추출기.

원문 표의 값 셀에는 <TE ACODE="..."> 또는 <TU AUNIT=".." AUNITVALUE=".."> 처럼
고정 코드가 붙어 있다. 표 묶음(TABLE-GROUP ACLASS)별로 행(dict) 리스트를 만든다.
TU 셀은 화면 표시값과 함께 코드값을 'KEY@'로 같이 담는다.
"""
import re

_TG = re.compile(r"<TABLE-GROUP\b([^>]*)>(.*?)</TABLE-GROUP>", re.S)
_TR = re.compile(r"<TR\b[^>]*>(.*?)</TR>", re.S)
_CELL = re.compile(r"<(TE|TU)\b([^>]*)>(.*?)</\1>", re.S)
_DOCNAME = re.compile(r"<DOCUMENT-NAME[^>]*>([^<]*)")


def _attr(a: str, name: str):
    m = re.search(name + r'="([^"]*)"', a)
    return m.group(1) if m else None


def clean(v: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", v)).strip()


def decode(raw: bytes) -> str:
    for enc in ("utf-8", "euc-kr", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def document_name(text: str):
    m = _DOCNAME.search(text)
    return m.group(1).strip() if m else None


def groups(text: str) -> dict:
    """{ACLASS: [row, ...]} — 같은 ACLASS가 여러 번 나오면 행을 이어 붙인다."""
    out = {}
    for a, body in _TG.findall(text):
        cls = _attr(a, "ACLASS") or "?"
        rows = []
        for tr in _TR.findall(body):
            d = {}
            for tag, ca, v in _CELL.findall(tr):
                if tag == "TE":
                    d[_attr(ca, "ACODE")] = clean(v)
                else:
                    k = _attr(ca, "AUNIT")
                    d[k] = clean(v)
                    d[k + "@"] = _attr(ca, "AUNITVALUE")
            if d:
                rows.append(d)
        out.setdefault(cls, []).extend(rows)
    return out


def first(g: dict, key: str, default=None):
    """모든 표 묶음에서 key가 처음 나오는 값."""
    for rows in g.values():
        for r in rows:
            if key in r and r[key] not in (None, ""):
                return r[key]
    return default


def num(v):
    """'1,234' → 1234, '-'/'' → None, '(1,000)' → 1000(괄호는 부가표기)."""
    if v is None:
        return None
    s = str(v).replace(",", "").replace(" ", "").strip()
    s = s.strip("()")
    if s in ("", "-", "—"):
        return None
    try:
        f = float(s)
        return int(f) if f.is_integer() else f
    except ValueError:
        return None


def ymd(v):
    """'2026.11.06' / '2026년 11월 06일' / '20261106' / '2026-11-06' → '2026-11-06'."""
    if not v:
        return None
    d = re.sub(r"\D", "", str(v))
    if len(d) >= 8:
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return None
