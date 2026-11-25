"""분기보고서 기준 실적을 수집해 dashboard/data/quarterly-results.json 에 분기별로 저장.

    python scripts/update_quarterly_results.py                   # 실적 시즌일 때만 해당 분기 수집, 아니면 건너뜀
    python scripts/update_quarterly_results.py --period 2026.3Q  # 시즌과 무관하게 지정 분기 수집

실적 시즌 = 분기 종료 다음 달 둘째 주 월요일 ~ 정기보고서 마감일(주말·공휴일이면 다음 영업일).
"""
import argparse, csv, json, os, sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from investment_data.calendar import DartClient, CollectionError
from investment_data.quarterly_results import REPORT_CODES, quarter_end, parse_rows, corp_codes, build_quarter, derive_q4, season_period

OUT = ROOT / 'dashboard/data/quarterly-results.json'
SNAPSHOT = ROOT / 'data/reference/quantiwise-calendar.json'
SECTORS = ROOT / 'collectors/stockdb/config/universe/stockeasy_sectors.csv'
CALENDAR = ROOT / 'dashboard/data/calendar-data.json'


def kr_holidays(today):
    try:
        import holidays  # 워크플로에서 pip install holidays
    except ImportError:
        raise CollectionError('공휴일 판정에 holidays 패키지가 필요합니다: pip install holidays') from None
    return holidays.KR(years=[today.year - 1, today.year])


def fetch(client, corps, year, reprt_code):
    rows = []
    for i in range(0, len(corps), 100):
        body = json.loads(client.request('fnlttMultiAcnt.json', dict(corp_code=','.join(corps[i:i + 100]), bsns_year=year, reprt_code=reprt_code)))
        if body.get('status') == '013':  # 해당 묶음에 아직 제출된 보고서 없음
            continue
        if body.get('status') != '000':
            raise CollectionError(f"DART fnlttMultiAcnt status {body.get('status')}: {body.get('message')}")
        rows.extend(body.get('list', []))
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--period')
    args = parser.parse_args()
    today = date.today()
    period = args.period or season_period(today, kr_holidays(today))
    if not period:
        print(f'{today}: 실적 시즌이 아니어서 분기보고서 수집을 건너뜁니다.')
        return
    year, q, end = quarter_end(period)
    key = os.environ.get('DART_API_KEY')
    if not key:
        raise CollectionError('DART_API_KEY is not configured')
    client = DartClient(key)

    snapshot = json.loads(SNAPSHOT.read_text(encoding='utf-8')) if SNAPSHOT.exists() else None
    with SECTORS.open(encoding='utf-8') as f:
        universe = {r['code'] for r in csv.DictReader(f)}
    universe |= {c['stockCode'] for c in (snapshot or {}).get('companies', [])}
    names = corp_codes(client.request('corpCode.xml', {}))
    corps = sorted(names[s][0] for s in universe if s in names)

    report = lambda n: parse_rows(fetch(client, corps, year, REPORT_CODES[n]), quarter_end(f'{year}.{n}Q')[2])
    current = report(q)
    previous = report(q - 1) if q > 1 else {}
    if q == 4:  # 사업보고서는 연간값이라 1~3Q를 빼서 4Q 3개월 값을 만듦
        current = derive_q4(current, [report(1), report(2), previous])
    rows = build_quarter(period, current, previous, names, snapshot)
    if not rows:
        raise CollectionError(f'{period}: 수집된 분기보고서가 없습니다; 기존 파일 유지')

    data = json.loads(OUT.read_text(encoding='utf-8')) if OUT.exists() else {'schemaVersion': 1, 'quarters': {}}
    if data['quarters'].get(period, {}).get('companies') == rows:
        print(f'{period}: 변경 없음 ({len(rows)}곳)')
        return
    data['quarters'][period] = {
        'generatedAt': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'source': 'OpenDART 다중회사 주요계정 (분기·반기보고서)',
        'universe': len(corps),
        'consensusSavedAt': (snapshot or {}).get('metadata', {}).get('savedRefreshAt'),
        'companies': rows,
    }
    tmp = OUT.with_suffix('.tmp')  # 쓰다 실패해도 기존 파일 보존
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    tmp.replace(OUT)

    # 점검: 잠정실적 공시가 있으면 영업이익을 비교해 수집 기준(3개월 vs 누적)이 맞는지 확인
    checked = mismatched = 0
    if CALENDAR.exists():
        prelim = {e['stock']: e['financials']['metrics']['operatingProfit']['value']
                  for e in json.loads(CALENDAR.read_text(encoding='utf-8'))['events']
                  if e.get('type') == 'earnings' and e.get('basis') == '연결' and (e.get('financials') or {}).get('period') == period}
        for r in rows:
            a, b = r['metrics']['operatingProfit']['value'], prelim.get(r['stock'])
            if r['basis'] == '연결' and a is not None and b is not None:
                checked += 1
                mismatched += abs(a - b) > max(1, abs(b) * 0.02)
    print(f'{period}: 분기보고서 {len(rows)}곳 / 대상 {len(corps)}곳, 컨센 보유 {sum(1 for r in rows if r["consensus"])}곳; '
          f'잠정실적 대비 영업이익 2% 이상 차이 {mismatched}/{checked}곳')


if __name__ == '__main__':
    try:
        main()
    except CollectionError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
