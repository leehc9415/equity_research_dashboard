"""Copy the exported stockDB website; never read or publish its database/config."""
import argparse
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sync(source: Path) -> None:
    source = source.resolve()
    destination = ROOT / 'dashboard' / 'markets'
    if source == destination.resolve():
        raise ValueError('Source must be the stockDB export, not the destination')
    html = source / 'index.html'
    overview = source / 'data' / 'overview.js'
    text = overview.read_text(encoding='utf-8')
    prefix = 'window.DASH_OVERVIEW='
    if not text.startswith(prefix):
        raise ValueError('Invalid overview.js export')
    data = json.loads(text[len(prefix):].strip().removesuffix(';'))
    groups = {g['id']: g for g in data['groups']}
    if not {'KR_SECTOR', 'US_SECTOR_ETF', 'MACRO'} <= groups.keys():
        raise ValueError('All three market groups are required')
    files = [html, overview]
    for card in groups['KR_SECTOR']['cards']:
        sector = card.get('sector')
        if sector:
            if not re.fullmatch(r'[a-z0-9_]+', sector):
                raise ValueError(f'Invalid sector identifier: {sector}')
            files.append(source / 'data' / f'kr_{sector}.js')
    # Verify the complete export before changing the published snapshot.
    for file in files:
        if not file.is_file() or not file.stat().st_size:
            raise ValueError(f'Missing or empty export: {file}')
    for file in files:
        target = destination / file.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, target)
    print(json.dumps({'files': len(files), 'generated': data.get('generated'),
                      'asOf': {k: v.get('asof') for k, v in groups.items()}}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='Path to stockDB/site')
    sync(parser.parse_args().source)
