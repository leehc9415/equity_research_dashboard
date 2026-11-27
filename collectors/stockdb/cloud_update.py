"""Collect, validate and export market data without exposing incomplete results."""
from __future__ import annotations

import argparse
import io
import json
import math
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from src.prices.db import connect, upsert_prices, log_run, DB_PATH
from src.prices import global_yf, naver_kr
from src.prices.run_prices import run_kr
from cloud_state import GitHubState, KIWOOM_NAME, load_kiwoom, pack

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[1]
WEB_DATA = REPO_ROOT / 'dashboard/markets/data'


def inventory(con):
    return {sid: (count, last) for sid, count, last in con.execute(
        "SELECT symbol_id,count(*),max(date) FROM prices_daily WHERE source NOT IN ('sector_idx','se_sector_idx') GROUP BY 1").fetchall()}


def validate_source(con, baseline):
    after = inventory(con)
    for sid, (count, last) in baseline.items():
        if sid not in after or after[sid][0] < count or after[sid][1] < last:
            raise ValueError(f'History shrank or moved backwards: {sid}')
    invalid = con.execute("""SELECT count(*) FROM prices_daily WHERE
        close IS NULL OR NOT isfinite(close) OR close<=0 OR
        adj_factor IS NULL OR NOT isfinite(adj_factor) OR adj_factor<=0""").fetchone()[0]
    if invalid:
        raise ValueError(f'Invalid prices/adjustment factors: {invalid} rows')
    if con.execute('SELECT count(DISTINCT sector_id) FROM sector_map WHERE included').fetchone()[0] != 21:
        raise ValueError('Expected all 21 Korean sectors')


def load_pc_kiwoom(con):
    """PC 가 올린 최신 키움 테이블(키움 키는 등록 IP 에서만 동작)을 받아 이 DB 의 키움 테이블을 바꾼다."""
    archive = ROOT / 'data/kiwoom-state.zip'
    load_kiwoom(GitHubState().download_latest(archive, KIWOOM_NAME), con)


def validate_kr_collection(con):
    """정규장 일봉이 최근 거래일까지 다 있는지. PC 수집이 안 됐으면 실패 → 다음 예약 슬롯이 다시 확인."""
    expected = naver_kr.last_trade_date().date()
    last = con.execute("SELECT max(date) FROM prices_daily WHERE source='kiwoom'").fetchone()[0]
    if last is None or last < expected:
        raise ValueError(f'PC Kiwoom upload is behind: latest {last} < {expected}. Check the PC daily run.')
    n = con.execute("SELECT count(DISTINCT symbol_id) FROM prices_kiwoom WHERE venue='REG' AND date=?",
                    [expected]).fetchone()[0]
    # 기준 = 직전 거래일의 정규장 일봉 종목 수 (prices_daily 30일 종목 수는 상장폐지·코넥스 등 키움 대상 밖이 섞여
    # 늘 3,771/4,077 처럼 95% 아래 → 2026-11-27 게시 실패)
    baseline = con.execute("""SELECT count(DISTINCT symbol_id) FROM prices_kiwoom WHERE venue='REG'
        AND date=(SELECT max(date) FROM prices_kiwoom WHERE venue='REG' AND date<?)""", [expected]).fetchone()[0]
    if n < max(1000, int(baseline * .95)):
        raise ValueError(f'Incomplete regular-session bars on {expected}: {n} of about {baseline}')
    for sid in ('KS11', 'KQ11'):
        last = con.execute('SELECT max(date) FROM prices_daily WHERE symbol_id=?', [sid]).fetchone()[0]
        if last is None or last < expected:
            raise ValueError(f'Benchmark is behind Korean stocks: {sid} {last} < {expected}')


def fetch_fred(sid, series, start):
    if not series or not str(series).isalnum():
        raise ValueError('Invalid configured FRED series')
    response = requests.get('https://fred.stlouisfed.org/graph/fredgraph.csv',
        params={'id': series, 'cosd': start}, timeout=45)
    response.raise_for_status()
    df = pd.read_csv(io.StringIO(response.text), na_values='.')
    if len(df.columns) != 2 or series not in df.columns:
        raise ValueError('Unexpected FRED CSV')
    values = pd.to_numeric(df[series], errors='coerce')
    days = pd.to_datetime(df.iloc[:, 0])
    valid = values.notna() & (values > 0) & (days <= global_yf._cutoff_date())
    values, days = values[valid], days[valid]
    return pd.DataFrame({'symbol_id': sid, 'date': days.dt.date, 'open': values,
        'high': values, 'low': values, 'close': values, 'adj_factor': 1.,
        'source': 'fred', 'value_is_est': True})


def apply_global(con, frames, baseline):
    """Reject missing/backwards symbols before changing any global prices."""
    for sid, frame in frames.items():
        if frame.empty or not frame['close'].map(lambda x: math.isfinite(x) and x > 0).all():
            raise ValueError(f'No valid global quotes: {sid}')
        if sid in baseline and max(frame['date']) < baseline[sid][1]:
            raise ValueError(f'Global provider moved backwards: {sid}')
    for sid, frame in frames.items():
        # Yahoo retroactively adjusts history after ETF distributions. Apply
        # the overlap's change to the older stored factors before upserting it.
        if frame['source'].eq('yf').all():
            first = frame.sort_values('date').iloc[0]
            old = con.execute('SELECT adj_factor FROM prices_daily WHERE symbol_id=? AND date=?',
                              [sid, first['date']]).fetchone()
            if old and old[0] and first['adj_factor'] > 0:
                ratio = float(first['adj_factor']) / old[0]
                if abs(ratio - 1) > 1e-7:
                    con.execute("UPDATE prices_daily SET adj_factor=adj_factor*? WHERE symbol_id=? AND date<?",
                                [ratio, sid, first['date']])
        upsert_prices(con, frame)


def delayed_fred_release(sid, frame, baseline, cutoff):
    """FRED Treasury releases can trail the market by a business day.

    Retain a newer stored rate for at most seven calendar days; never rewrite
    it with older data, and never apply this exception to stocks or ETFs.
    """
    if sid not in {'UST2Y', 'UST5Y', 'UST10Y', 'UST30Y'} or sid not in baseline or frame.empty:
        return False
    if not frame['source'].eq('fred').all():
        return False
    latest = max(frame['date'])
    retained = baseline[sid][1]
    return latest < retained <= cutoff and (cutoff - retained).days <= 7 and (cutoff - latest).days <= 7


def collect_global(con, baseline):
    symbols = con.execute("SELECT id,ticker,fallback_source,fallback_ticker FROM symbols WHERE market IN ('US','GLOBAL') AND source='yf' AND active").df()
    start = (pd.Timestamp.now(tz='Asia/Seoul').tz_localize(None) - pd.Timedelta(days=14)).strftime('%Y-%m-%d')
    data, failed = global_yf.fetch_yf(symbols[['id', 'ticker']], start)
    frames = {sid: frame.copy() for sid, frame in data.groupby('symbol_id')} if not data.empty else {}
    errors, deferred = [], set()
    for sid in failed:
        row = symbols[symbols['id'] == sid].iloc[0]
        frame = pd.DataFrame()
        try:
            import yfinance as yf
            for attempt in range(2):
                try:
                    raw = yf.Ticker(row['ticker']).history(start=start, auto_adjust=False, repair=False, raise_errors=True, timeout=20)
                    frame = global_yf._from_frame(sid, raw, 'yf')
                    if not frame.empty:
                        break
                except Exception:
                    pass
                time.sleep(2 * (attempt + 1))
            if frame.empty and row['fallback_source'] == 'fred':
                frame = fetch_fred(sid, row['fallback_ticker'], start)
            if frame.empty and row['fallback_source'] == 'fdr':
                frame = global_yf.fetch_fdr(sid, row['fallback_ticker'], start)
        except Exception:
            pass
        if frame.empty:
            errors.append(sid)
        elif delayed_fred_release(sid, frame, baseline, global_yf._cutoff_date().date()):
            deferred.add(sid)
            print(f'{sid}: FRED through {max(frame["date"])}; retained newer stored {baseline[sid][1]}')
        else:
            frames[sid] = frame
    if errors or set(frames) | deferred != set(symbols['id']):
        raise RuntimeError('Global collection incomplete; keeping published data: ' + ','.join(errors))
    apply_global(con, frames, baseline)
    combined = pd.concat(frames.values(), ignore_index=True)
    log_run(con, 'global', 'ok_delayed_fred' if deferred else 'ok', combined, note=','.join(sorted(deferred)))
    print(f'Global quotes verified: {len(frames)} updated / {len(deferred)} awaiting FRED release / {len(combined)} rows')


def rebuild(con):
    from src.indicators import build
    from src.indicators.sector_index import build_sector_index
    from src.catalog import VIEW_SQL, apply_comments
    # Retain the original complete symbol master, including delisted names.
    # The desktop catalog loader otherwise replaces it from local raw files.
    build_sector_index(con)
    for part in range(8):
        print(f'Daily indicators: part {part + 1}/8', flush=True)
        build.build_daily(con, part, 8)
    print('Building RS ranks', flush=True)
    build.build_rs(con)
    print('Building weekly/monthly bars', flush=True)
    build._bars(con, 'bars_weekly', 'week', build.WEEKLY_MA, 10)
    build._bars(con, 'bars_monthly', 'month', build.MONTHLY_MA, None)
    con.execute(VIEW_SQL)
    apply_comments(con)
    print('Indicator rebuild complete', flush=True)


KR_GROUPS = ('KR_SECTOR',)
GLOBAL_GROUPS = ('US_SECTOR_ETF', 'MACRO')


def keep_published_groups(staging: Path, destination: Path, keep):
    """새 overview.js 에서 keep 그룹을 직전 게시본 것으로 되돌린다. 한국(PC)·해외(클라우드) 갱신이
    서로 다른 DB 로 overview.js 를 만들므로, 각자 자기 그룹만 바꾸고 상대 그룹은 게시본을 유지한다."""
    from validate_market_data import read_assignment
    if not keep or not (destination / 'overview.js').exists():
        return
    prefix = 'window.DASH_OVERVIEW='
    old = {g['id']: g for g in read_assignment(destination / 'overview.js', prefix)['groups']}
    new = read_assignment(staging / 'overview.js', prefix)
    new['groups'] = [old.get(g['id'], g) if g['id'] in keep else g for g in new['groups']]
    (staging / 'overview.js').write_text(prefix + json.dumps(new, ensure_ascii=False, separators=(',', ':')) + ';\n',
                                         encoding='utf-8')


def publish_export(con, destination=WEB_DATA, kr=True, local=False):
    """kr=False(클라우드 해외 갱신): 한국 그룹은 게시본 유지. local=True(PC 한국 갱신): 해외 그룹은 게시본 유지."""
    from src.dashboard import export as exporter
    import sys
    sys.path.insert(0, str(REPO_ROOT / 'scripts'))
    from validate_market_data import validate_directory, read_assignment
    with tempfile.TemporaryDirectory(dir=ROOT / 'data') as temp:
        previous_site = exporter.SITE
        exporter.SITE = Path(temp)
        if not kr:
            # 해외 갱신: 국내 파일은 직전 게시본을 그대로 두고 overview.js만 새로 만든다.
            (Path(temp) / 'data').mkdir(parents=True, exist_ok=True)
            for file in Path(destination).glob('*.js'):
                shutil.copyfile(file, Path(temp) / 'data' / file.name)
        try:
            exporter.export(con, kr=kr)
        finally:
            exporter.SITE = previous_site
        staging = Path(temp) / 'data'
        keep_published_groups(staging, Path(destination), GLOBAL_GROUPS if local else () if kr else KR_GROUPS)
        validate_directory(staging, baseline=destination)
        new = read_assignment(staging / 'overview.js', 'window.DASH_OVERVIEW=')
        # Do not create a deployment solely because the generation time changed.
        if (destination / 'overview.js').exists():
            old = read_assignment(destination / 'overview.js', 'window.DASH_OVERVIEW=')
            if {k: v for k, v in old.items() if k != 'generated'} == {k: v for k, v in new.items() if k != 'generated'}:
                (staging / 'overview.js').write_bytes((destination / 'overview.js').read_bytes())
        destination.mkdir(parents=True, exist_ok=True)
        for file in staging.glob('*.js'):
            shutil.copyfile(file, destination / file.name)
    validate_directory(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--region', choices=['kr', 'global', 'all'], default='all')
    parser.add_argument('--export-only', action='store_true', help='Rebuild a restored snapshot without network collection')
    parser.add_argument('--output', type=Path, default=WEB_DATA, help='Validated web export destination')
    parser.add_argument('--local', action='store_true',
                        help='PC: Korean region from the local DB (Kiwoom already collected), keep published global groups, no state pack')
    args = parser.parse_args()
    if args.local and args.region != 'kr':
        parser.error('--local only supports --region kr')
    con = connect(DB_PATH)
    try:
        con.execute("SET TimeZone='Asia/Seoul'" if args.local else "SET threads=2; SET memory_limit='4GB'; SET TimeZone='Asia/Seoul'")
        before = inventory(con)
        if not args.export_only:
            if args.region in ('kr', 'all'):
                if not args.local:
                    load_pc_kiwoom(con)
                run_kr(con, None, collect=False)
                validate_kr_collection(con)
            if args.region in ('global', 'all'):
                collect_global(con, before)
        validate_source(con, before)
        rebuild(con)
        publish_export(con, args.output, kr=args.region != 'global', local=args.local)
        con.execute('CHECKPOINT')
    finally:
        con.close()
    if args.local:
        print('Verified Korean export is ready.')
        return
    pack(DB_PATH, ROOT / 'data/market-state-next.zip')
    print('Verified export and next source state are ready.')


if __name__ == '__main__':
    main()
