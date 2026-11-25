"""스탁이지 대·중분류(collectors/stockdb/config/universe/stockeasy_sectors.csv)를 화면용 JSON으로 내보냄.

    python scripts/build_stockeasy_sectors.py
"""
import csv, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'collectors/stockdb/config/universe/stockeasy_sectors.csv'
OUT = ROOT / 'dashboard/data/stockeasy-sectors.json'

with SRC.open(encoding='utf-8') as f:
    sectors = {r['code']: [r['se_l'], r['se_m']] for r in csv.DictReader(f) if r['se_l']}
OUT.write_text(json.dumps({'source': SRC.name, 'sectors': sectors}, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
print(f'{len(sectors)} stocks -> {OUT.relative_to(ROOT)}')
