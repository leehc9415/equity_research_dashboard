from pathlib import Path
import json, os, sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from investment_data.calendar import DartClient, CollectionError, collect, atomic_write
from investment_data.calendar_quantiwise import enrich_calendar

def main():
    key=os.environ.get('DART_API_KEY')
    if not key:raise CollectionError('DART_API_KEY is not configured')
    path=ROOT/'dashboard/data/calendar-data.json'
    current=json.loads(path.read_text(encoding='utf-8'))
    updated,count=collect(current,DartClient(key,ROOT/'data/local/calendar/documents'))
    reference=ROOT/'data/reference/quantiwise-calendar.json'
    if reference.exists():enrich_calendar(updated,json.loads(reference.read_text(encoding='utf-8')))
    if updated!=current:atomic_write(path,updated)
    pending=updated.get('pendingDocuments',[])
    print(f'DART collection: {count} new filings; {len(updated["events"])} events; {len(pending)} originals pending')
    for item in pending:
        print(f'Original pending: {item["filing"]["rcept_no"]} [status {item["status"]}]; retained for retry')
    if pending:print(f'::warning::DART originals pending: {len(pending)}. Other verified filings were saved; pending receipts will retry.')
    summary=os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary,'a',encoding='utf-8') as stream:
            stream.write(f'### DART calendar\n\nNew filings: {count} · Calendar events: {len(updated["events"])} · Originals pending: {len(pending)}\n\n')
            for item in pending:stream.write(f'- Receipt {item["filing"]["rcept_no"]}: status {item["status"]}, retry retained (attempt {item["attempts"]}).\n')

if __name__=='__main__':
    try:main()
    except CollectionError as error:print(str(error),file=sys.stderr);sys.exit(1)
