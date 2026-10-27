from pathlib import Path
from datetime import date
import copy,json,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from investment_data.calendar import CollectionError,parse_filing,collect,merge,atomic_write,validate

def filing(receipt='20261027800001',title='결산실적공시 예고(안내공시)'):
    return dict(corp_name='샘플기업',stock_code='000020',corp_code='00100000',corp_cls='Y',rcept_no=receipt,rcept_dt='20261027',report_nm=title)
NOTICE='<table><tr><td>2. 결산대상기간</td><td>2026-07-01</td><td>2026-09-30</td></tr><tr><td>3. 결산실적 공시예정일</td><td>2026-10-30</td></tr></table>'
IR='<table><tr><td>1. 일시</td><td>행사일</td></tr><tr><td>시작일</td><td>종료일</td></tr><tr><td>2026-10-28</td><td>2026-10-30</td><td>09:00</td></tr><tr><td>4. 실시목적</td><td>NDR</td></tr></table>'

class Client:
    def __init__(self,items,documents):self.items=items;self.documents=documents;self.calls=0
    def filings(self,start,end):return self.items
    def document(self,receipt):self.calls+=1;return self.documents[receipt]

class CalendarTests(unittest.TestCase):
    def test_future_date_comes_from_body_and_reprocessing_is_idempotent(self):
        f=filing();c=Client([f],{f['rcept_no']:NOTICE});base=dict(schemaVersion=1,events=[])
        first,count=collect(base,c,date(2026,10,28));self.assertEqual(first['events'][0]['date'],'2026-10-30');self.assertEqual(count,1)
        second,count=collect(first,c,date(2026,10,28));self.assertEqual(second,first);self.assertEqual(count,0);self.assertEqual(c.calls,1)
    def test_failed_document_does_not_mutate_input_or_saved_file(self):
        data=dict(schemaVersion=1,events=[]);before=copy.deepcopy(data);f=filing();c=Client([f],{f['rcept_no']:'<html>unrecognized</html>'})
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'calendar.json';atomic_write(path,data);saved=path.read_bytes()
            with self.assertRaises(CollectionError):collect(data,c,date(2026,10,28))
            self.assertEqual(path.read_bytes(),saved);self.assertEqual(data,before)
    def test_ir_spans_days_and_correction_moves_original_without_duplicates(self):
        events=[];original=parse_filing(filing(title='기업설명회(IR)개최'),IR);merge(events,original);self.assertEqual(len(events),3)
        revised=dict(original,receipt='20261028800002',received='20261028',date='2026-11-02',endDate='2026-11-02',originalReceived='20261027',isCorrection=True)
        merge(events,revised);self.assertEqual(len(events),1);self.assertEqual(events[0]['date'],'2026-11-02')
    def test_unsafe_link_is_rejected(self):
        e=parse_filing(filing(),NOTICE);e['url']='https://attacker.example/report'
        with self.assertRaises(CollectionError):validate(dict(schemaVersion=1,events=[e]))
    def test_unordered_individual_ir_dates_do_not_fill_unspecified_days(self):
        text='<table><tr><td>1. 일시 및 장소</td><td>일시</td><td>2026-11-12 09:00 2026-11-09 16:00</td></tr></table>'
        e=parse_filing(filing(title='기업설명회(IR)개최'),text);events=[];merge(events,e)
        self.assertEqual([x['date'] for x in events],['2026-11-09','2026-11-12'])
    def test_same_quarter_can_have_distinct_preliminary_release_dates(self):
        base=parse_filing(filing(),NOTICE);base.update(status='published',date='2026-10-07',basis='연결',financials=dict(period='2026.3Q',history=[],consensus=None))
        events=[base];new=copy.deepcopy(base);new.update(date='2026-10-30',receipt='20261030800001');merge(events,new);self.assertEqual(len(events),2)

if __name__=='__main__':unittest.main()
