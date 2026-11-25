"""분기보고서(정기공시) 기준 분기 실적: OpenDART 다중회사 주요계정(fnlttMultiAcnt)을 100개사씩 모아 수집."""
import re
from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZipFile

# 4Q는 사업보고서 연간값에서 3Q 누적을 빼야 해서 아직 지원하지 않음.
REPORT_CODES = {1: '11013', 2: '11012', 3: '11014'}
ACCOUNTS = {
    'revenue': ('매출액', '영업수익', '수익(매출액)'),
    'operatingProfit': ('영업이익', '영업이익(손실)'),
    'netIncome': ('당기순이익', '당기순이익(손실)'),
}
METRICS = tuple(ACCOUNTS)


def quarter_end(period):
    """'2026.3Q' -> (2026, 3, '2026.09.30')"""
    m = re.fullmatch(r'(20\d{2})\.([1-4])Q', period or '')
    if not m or int(m[2]) not in REPORT_CODES:
        raise ValueError(f'지원하지 않는 분기: {period} (1Q~3Q만 지원)')
    year, q = int(m[1]), int(m[2])
    return year, q, f'{year}.{q * 3:02}.{30 if q in (2, 3) else 31}'


def amount(text):
    """DART 금액 문자열(원) -> 억원. 빈값·'-'는 None."""
    text = (text or '').replace(',', '').strip()
    if text in ('', '-'):
        return None
    negative = text.startswith('(') and text.endswith(')')
    value = int(text.strip('()')) * (-1 if negative else 1)
    return round(value / 1e8, 2)


def parse_rows(rows, end_date):
    """주요계정 응답 행 -> {stock: {basis, receipt, values{metric: (당기 3개월, 전년동기 3개월)}}}. 연결(CFS) 우선."""
    by_stock = {}
    for r in rows:
        if r.get('sj_div') not in ('IS', 'CIS') or not r.get('stock_code', '').strip():
            continue
        # 결산월이 다른 회사는 같은 보고서 코드라도 다른 기간이므로 제외
        if end_date not in (r.get('thstrm_dt') or ''):
            continue
        # shortcut: USD 등 외화 보고사(두산밥캣 등)는 환율 환산 없이 제외, 필요해지면 환산 추가
        if (r.get('currency') or 'KRW') != 'KRW':
            continue
        metric = next((k for k, names in ACCOUNTS.items() if r.get('account_nm', '').replace(' ', '') in names), None)
        if not metric:
            continue
        entry = by_stock.setdefault(r['stock_code'].strip(), {}).setdefault(r.get('fs_div'), {'receipt': r.get('rcept_no'), 'values': {}})
        entry['values'].setdefault(metric, (amount(r.get('thstrm_amount')), amount(r.get('frmtrm_amount'))))
    result = {}
    for stock, divs in by_stock.items():
        div = 'CFS' if 'CFS' in divs else 'OFS'
        if div in divs:
            result[stock] = dict(basis='연결' if div == 'CFS' else '별도', **divs[div])
    return result


def corp_codes(zip_bytes):
    """corpCode.xml zip -> {stock_code: (corp_code, corp_name)}"""
    with ZipFile(BytesIO(zip_bytes)) as archive:
        root = ElementTree.fromstring(archive.read(archive.namelist()[0]))
    return {e.findtext('stock_code').strip(): (e.findtext('corp_code'), e.findtext('corp_name'))
            for e in root.iter('list') if (e.findtext('stock_code') or '').strip()}


def build_quarter(period, current, previous, names, snapshot):
    """현재 분기 parse 결과 + 직전 분기 parse 결과 + Quantiwise 스냅샷 -> 화면용 행 목록."""
    year, q, _ = quarter_end(period)
    key = f'{year}{q * 3:02}'
    saved = {c['stockCode']: c for c in (snapshot or {}).get('companies', [])}
    rows = []
    for stock, item in sorted(current.items()):
        prev = previous.get(stock)
        same_basis = prev and prev['basis'] == item['basis']
        qw = saved.get(stock) or {}
        usable = qw.get('fiscalMonth') == 12 and not qw.get('localCurrencyUnverified')
        consensus = (qw.get('consensus') or {}).get(key) if usable else None
        corp, name = names.get(stock, (None, stock))
        rows.append(dict(
            stock=stock, corp=corp, company=name, basis=item['basis'], receipt=item['receipt'],
            filed=f"{item['receipt'][:4]}-{item['receipt'][4:6]}-{item['receipt'][6:8]}" if item.get('receipt') else None,
            metrics={m: dict(value=item['values'].get(m, (None, None))[0], priorYear=item['values'].get(m, (None, None))[1],
                             previous=prev['values'].get(m, (None, None))[0] if same_basis else None) for m in METRICS},
            consensus={m: consensus.get(m) for m in METRICS} if consensus and any(consensus.get(m) is not None for m in METRICS) else None,
            marketCap=qw.get('marketCap'),
        ))
    return rows
