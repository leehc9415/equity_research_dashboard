from pathlib import Path
import json, os, sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from investment_data.calendar import DartClient, CollectionError, collect, atomic_write

def main():
    key=os.environ.get('DART_API_KEY')
    if not key:raise CollectionError('DART_API_KEY is not configured')
    path=ROOT/'dashboard/data/calendar-data.json'
    current=json.loads(path.read_text(encoding='utf-8'))
    updated,count=collect(current,DartClient(key,ROOT/'data/local/calendar/documents'))
    if updated!=current:atomic_write(path,updated)
    print(f'DART collection complete: {count} new filings; {len(updated["events"])} events')

if __name__=='__main__':
    try:main()
    except CollectionError as error:print(str(error),file=sys.stderr);sys.exit(1)
