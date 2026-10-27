from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from investment_data.calendar import validate
if __name__=='__main__':
    path=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'dashboard/data/calendar-data.json'
    data=json.loads(path.read_text(encoding='utf-8'));validate(data)
    print(f'Calendar valid: {len(data["events"])} events')
