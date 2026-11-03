"""Validate Market Charts exports without executing their JavaScript."""
from __future__ import annotations

import argparse
import json
import math
import re
from datetime import date
from pathlib import Path

DEFAULT = Path(__file__).resolve().parents[1] / 'dashboard/markets/data'
GROUPS = {'KR_SECTOR', 'US_SECTOR_ETF', 'MACRO'}


def read_assignment(path, prefix):
    text = Path(path).read_text(encoding='utf-8').strip()
    if not text.startswith(prefix) or not text.endswith(';'):
        raise ValueError(f'Invalid export assignment: {Path(path).name}')
    def reject(value):
        raise ValueError(f'Non-finite JSON number: {value}')
    return json.loads(text[len(prefix):-1], parse_constant=reject)


def validate_cards(cards):
    ids = set()
    if not cards:
        raise ValueError('Empty market card list')
    for card in cards:
        sid = card['id']
        if not sid or sid in ids:
            raise ValueError(f'Duplicate/empty market ID: {sid}')
        ids.add(sid)
        date.fromisoformat(card['date'])
        for name in ('chg_1d', 'ret_1m', 'ret_3m', 'rel_3m', 'dd_52w'):
            value = card.get(name)
            if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value)):
                raise ValueError(f'Invalid {name}: {sid}')
        if card.get('rs') is not None and not 0 <= card['rs'] <= 99:
            raise ValueError(f'Invalid RS: {sid}')
        for tf in ('d', 'w', 'm'):
            series = card['series'][tf]
            times, closes = series['t'], series['c']
            if not times or len(times) != len(closes) or not 0 <= series['k'] < len(closes):
                raise ValueError(f'Missing or mismatched {tf} series: {sid}')
            if any(not isinstance(x, int) or x <= 0 for x in times):
                raise ValueError(f'Non-increasing {tf} dates: {sid}')
            if any(not isinstance(x, (int, float)) or not math.isfinite(x) or x <= 0 for x in closes):
                raise ValueError(f'Invalid {tf} closes: {sid}')
        if any(x not in (None, 0, 1) for x in (card.get('badges') or {}).values()):
            raise ValueError(f'Invalid trend badge: {sid}')
    return ids


def validate_directory(directory=DEFAULT, baseline=None):
    directory = Path(directory)
    overview = read_assignment(directory / 'overview.js', 'window.DASH_OVERVIEW=')
    groups = overview['groups']
    if len(groups) != 3 or {g['id'] for g in groups} != GROUPS:
        raise ValueError('Expected exactly three market groups')
    # 차트 주황선: 일봉 120일선 (2026-11-03 이전 내보내기는 50일선)
    if overview.get('ma') not in ({'d': 120, 'w': 30, 'm': 10}, {'d': 50, 'w': 30, 'm': 10}):
        raise ValueError('Unexpected moving-average definition')
    if overview.get('badge_ma', {'d': 50, 'w': 30, 'm': 10}) != {'d': 50, 'w': 30, 'm': 10}:
        raise ValueError('Unexpected trend-badge definition')
    old_groups = {}
    if baseline and (Path(baseline) / 'overview.js').exists():
        old_groups = {g['id']: g for g in read_assignment(Path(baseline) / 'overview.js', 'window.DASH_OVERVIEW=')['groups']}
    sectors = set()
    total = 0
    for group in groups:
        ids = validate_cards(group['cards'])
        if group['asof'] != max(c['date'] for c in group['cards']):
            raise ValueError(f"Incorrect group date: {group['id']}")
        old = old_groups.get(group['id'])
        if old:
            old_dates = {c['id']: c['date'] for c in old['cards']}
            if not set(old_dates) <= ids or any(c['date'] < old_dates.get(c['id'], '') for c in group['cards']):
                raise ValueError(f"Missing or backwards market cards: {group['id']}")
        for card in group['cards']:
            sector = card.get('sector')
            if not sector:
                continue
            if not re.fullmatch('[a-z0-9_]+', sector) or sector in sectors:
                raise ValueError('Duplicate/unsafe sector identifier')
            sectors.add(sector)
            prefix = f'window.DASH_SECTOR=window.DASH_SECTOR||{{}};window.DASH_SECTOR[{json.dumps(sector)}]='
            detail = read_assignment(directory / f'kr_{sector}.js', prefix)
            validate_cards(detail['cards'])
            if detail['sector'] != sector or len(detail['cards']) != card['count']:
                raise ValueError(f'Sector membership/count mismatch: {sector}')
            if baseline and (Path(baseline) / f'kr_{sector}.js').exists():
                previous = read_assignment(Path(baseline) / f'kr_{sector}.js', prefix)
                old_dates = {c['id']: c['date'] for c in previous['cards']}
                current = {c['id']: c['date'] for c in detail['cards']}
                if not old_dates.keys() <= current.keys() or any(current[k] < v for k, v in old_dates.items()):
                    raise ValueError(f'Sector history disappeared or moved backwards: {sector}')
            total += len(detail['cards'])
    if len(sectors) != 21:
        raise ValueError('Expected 21 Korean sector files')
    print(f'Market data valid: {len(groups)} groups / {len(sectors)} sectors / {total} stock cards')
    return overview


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=DEFAULT)
    args = parser.parse_args()
    validate_directory(args.directory)
