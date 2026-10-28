from pathlib import Path
from datetime import date
import copy,json,sys,tempfile,unittest
from unittest.mock import patch
from io import BytesIO
from zipfile import ZipFile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from investment_data.calendar import CollectionError,DocumentUnavailable,DartClient,parse_filing,collect,merge,atomic_write,validate
from investment_data.calendar_quantiwise import enrich_calendar
from investment_data.calendar_lifecycle import reconcile_earnings,reporting_period

def filing(receipt='20261027800001',title='결산실적공시 예고(안내공시)'):
    return dict(corp_name='샘플기업',stock_code='000020',corp_code='00100000',corp_cls='Y',rcept_no=receipt,rcept_dt='20261027',report_nm=title)
NOTICE='<table><tr><td>2. 결산대상기간</td><td>2026-07-01</td><td>2026-09-30</td></tr><tr><td>3. 결산실적 공시예정일</td><td>2026-10-30</td></tr></table>'
IR='<table><tr><td>1. 일시</td><td>행사일</td></tr><tr><td>시작일</td><td>종료일</td></tr><tr><td>2026-10-28</td><td>2026-10-30</td><td>09:00</td></tr><tr><td>4. 실시목적</td><td>NDR</td></tr></table>'

class Client:
    def __init__(self,items,documents):self.items=items;self.documents=documents;self.calls=0
    def filings(self,start,end):return self.items
    def document(self,receipt):self.calls+=1;return self.documents[receipt]

class CalendarTests(unittest.TestCase):
    def lifecycle_pair(self):
        notice=parse_filing(filing(receipt='20261019800001'),NOTICE)
        notice.update(period='2026.01~09',received='20261019',date='2026-10-26')
        result=dict(notice,status='published',receipt='20261026800001',received='20261026',basis='연결',financials=dict(period='2026.3Q',history=[],consensus=None))
        result.pop('period')
        return notice,result
    def test_cumulative_notice_matches_quarter_result_and_preserves_receipt_history(self):
        notice,result=self.lifecycle_pair();events=[notice,result]
        self.assertEqual(reporting_period(notice),'2026.3Q')
        self.assertEqual(reconcile_earnings(events),1);self.assertEqual(events,[result])
        self.assertEqual(result['announcements'][0]['receipt'],notice['receipt'])
        self.assertEqual(reconcile_earnings(events),0);self.assertEqual(len(result['announcements']),1)
        correction=copy.deepcopy(result);correction.update(receipt='20261028800001');correction.pop('announcements')
        merge(events,correction);self.assertEqual(events[0]['announcements'][0]['receipt'],notice['receipt'])
    def test_no_new_filings_still_reconciles_existing_unresolved_notices(self):
        notice,result=self.lifecycle_pair();base=dict(schemaVersion=1,events=[notice,result],collection=dict(through='2026-10-28'))
        updated,count=collect(base,Client([],{}),date(2026,10,28))
        self.assertEqual(count,0);self.assertEqual(len(updated['events']),1)
        self.assertEqual(updated['collection']['resolvedNotices'],1)
        self.assertIn(notice['receipt'],updated['processedReceipts']);self.assertEqual(len(base['events']),2)
    def test_delayed_release_resolves_notice_by_period_not_receipt_day(self):
        notice,result=self.lifecycle_pair();result['date']='2026-10-27';events=[notice,result]
        self.assertEqual(reconcile_earnings(events),1)
    def test_other_period_or_new_notice_after_old_release_is_not_removed(self):
        notice,result=self.lifecycle_pair();notice['period']='2026.4Q';events=[notice,result]
        self.assertEqual(reconcile_earnings(events),0)
        notice['period']='2026.01~09';notice['received']='20261028'
        self.assertEqual(reconcile_earnings(events),0)
    def test_past_dates_and_ir_do_not_imply_an_earnings_release(self):
        notice,result=self.lifecycle_pair();ir=dict(result,type='ir',status='scheduled');events=[notice,ir]
        self.assertEqual(reconcile_earnings(events),0);self.assertEqual(len(events),2)
        unknown=copy.deepcopy(notice);unknown.pop('period');self.assertEqual(reconcile_earnings([unknown,result]),0)
    def test_unavailable_original_does_not_block_other_filings_and_is_not_marked_processed(self):
        first=filing();second=filing(receipt='20261027800002')
        class WaitingClient(Client):
            def document(self,receipt):
                if receipt==first['rcept_no']:raise DocumentUnavailable(receipt,'014')
                return NOTICE
        base=dict(schemaVersion=1,events=[])
        updated,count=collect(base,WaitingClient([first,second],{}),date(2026,10,28))
        self.assertEqual(count,1);self.assertEqual(len(updated['events']),1)
        self.assertNotIn(first['rcept_no'],updated['processedReceipts'])
        self.assertFalse(updated['collection']['complete'])
        self.assertEqual(updated['pendingDocuments'][0]['filing']['rcept_no'],first['rcept_no'])
        self.assertEqual(base,dict(schemaVersion=1,events=[]))
    def test_pending_original_retries_outside_list_window_and_clears_after_success(self):
        original=filing(receipt='20260827800001')
        queued=dict(filing=original,status='013',firstSeen='2026-08-27',lastAttemptAt='2026-08-27T00:00:00Z',attempts=1)
        base=dict(schemaVersion=1,events=[],processedReceipts=[],pendingDocuments=[queued],collection=dict(through='2026-10-27'))
        updated,count=collect(base,Client([],{original['rcept_no']:NOTICE}),date(2026,10,28))
        self.assertEqual(count,1);self.assertEqual(updated['pendingDocuments'],[])
        self.assertIn(original['rcept_no'],updated['processedReceipts']);self.assertTrue(updated['collection']['complete'])
        again,count=collect(updated,Client([],{}),date(2026,10,28));self.assertEqual(count,0);self.assertEqual(again,updated)
    def test_xml_missing_file_response_retries_then_recovers(self):
        archive=BytesIO()
        with ZipFile(archive,'w') as zipped:zipped.writestr('report.xml',NOTICE)
        client=DartClient('test-key')
        with patch.object(client,'request',side_effect=[b'<result><status>014</status></result>',archive.getvalue()]) as request,patch('investment_data.calendar.time.sleep'):
            self.assertEqual(client.document('20261028800215'),NOTICE);self.assertEqual(request.call_count,2)
    def test_xml_pending_is_typed_but_auth_limit_and_bad_responses_are_fatal(self):
        client=DartClient('test-key')
        with patch.object(client,'request',return_value=b'<result><status>013</status></result>') as request,patch('investment_data.calendar.time.sleep'):
            with self.assertRaises(DocumentUnavailable) as context:client.document('20261028800215')
            self.assertEqual(context.exception.status,'013');self.assertEqual(request.call_count,3)
        for raw in (b'<result><status>010</status><message>test-key</message></result>',b'<result><status>020</status></result>',b'<html>unknown response</html>'):
            with self.subTest(raw=raw),patch.object(client,'request',return_value=raw) as request:
                with self.assertRaises(CollectionError) as context:client.document('20261028800215')
                self.assertNotIsInstance(context.exception,DocumentUnavailable)
                self.assertNotIn('test-key',str(context.exception));self.assertEqual(request.call_count,1)
    def test_pending_retry_preserves_first_seen_and_updates_attempts(self):
        original=filing()
        class WaitingClient(Client):
            def document(self,receipt):raise DocumentUnavailable(receipt,'013')
        one,_=collect(dict(schemaVersion=1,events=[]),WaitingClient([original],{}),date(2026,10,27))
        two,count=collect(one,WaitingClient([],{}),date(2026,10,28))
        self.assertEqual(count,0);self.assertEqual(two['pendingDocuments'][0]['firstSeen'],'2026-10-27')
        self.assertEqual(two['pendingDocuments'][0]['attempts'],2)
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
    def test_dart_correction_keeps_approved_matching_supplement(self):
        base=parse_filing(filing(),NOTICE)
        consensus=dict(period='2026.3Q',metrics=dict(operatingProfit=100),source='Quantiwise')
        base.update(status='published',basis='연결',marketCap=5000,marketCapPeriod='202609',financials=dict(period='2026.3Q',consensus=consensus,history=[dict(period='2026.1Q',source='Quantiwise',operatingProfit=80)],historyHasQuantiwise=True))
        revised=copy.deepcopy(base);revised.update(receipt='20261028800002');revised.pop('marketCap');revised.pop('marketCapPeriod')
        revised['financials']=dict(period='2026.3Q',consensus=None,history=[dict(period='2026.3Q',source='DART',operatingProfit=120)])
        events=[base];merge(events,revised)
        validate(dict(schemaVersion=1,publicDataSources=['OpenDART','Quantiwise'],events=events))
        self.assertEqual(len(events),1);self.assertEqual(events[0]['marketCap'],5000)
        self.assertEqual(events[0]['financials']['consensus'],consensus)
        self.assertEqual([h['period'] for h in events[0]['financials']['history']],['2026.3Q','2026.1Q'])
        with self.assertRaises(CollectionError):validate(dict(schemaVersion=1,publicDataSources=['OpenDART'],events=events))
    def test_newly_collected_company_joins_saved_quantiwise_by_stock_and_quarter(self):
        event=parse_filing(filing(),NOTICE)
        event.update(status='published',financials=dict(period='2026.3Q',metrics={m:dict(value=120,previous=100,priorYear=80) for m in ('revenue','operatingProfit','netIncome')},history=[],consensus=None))
        snapshot=dict(metadata=dict(savedRefreshAt='2026-10-28T03:01:29+09:00',marketCapColumn=dict(period='202609'),columns=[dict(kind='consensus',period='202609',baseDateSetting='CPD-1TD')]),companies=[dict(stockCode='000020',fiscalMonth=12,marketCap=5000,actuals={'202509':dict(revenue=80,operatingProfit=80,netIncome=80),'202512':dict(revenue=90,operatingProfit=90,netIncome=90),'202603':dict(revenue=95,operatingProfit=95,netIncome=95),'202606':dict(revenue=100,operatingProfit=100,netIncome=100)},consensus={'202609':dict(revenue=130,operatingProfit=130,netIncome=130)})])
        data=dict(schemaVersion=1,events=[event]);enrich_calendar(data,snapshot)
        self.assertEqual(event['marketCap'],5000);self.assertEqual(len(event['financials']['history']),5)
        self.assertEqual(event['financials']['consensus']['metrics']['operatingProfit'],130)
        self.assertEqual(event['financials']['metrics']['operatingProfit']['value'],120)
        before=copy.deepcopy(data);enrich_calendar(data,snapshot);self.assertEqual(data,before)
        event['financials']['period']='2026.4Q';enrich_calendar(data,snapshot)
        self.assertIsNone(event['financials']['consensus'])
    def test_reference_basis_mismatch_does_not_override_dart_or_show_comparable_consensus(self):
        event=parse_filing(filing(),NOTICE)
        event.update(status='published',financials=dict(period='2026.3Q',metrics={m:dict(value=120,previous=100,priorYear=80) for m in ('revenue','operatingProfit','netIncome')},history=[]))
        snapshot=dict(metadata={},companies=[dict(stockCode='000020',fiscalMonth=12,actuals={'202606':dict(revenue=1000,operatingProfit=1000,netIncome=1000)},consensus={'202609':dict(revenue=130,operatingProfit=130,netIncome=130)})])
        enrich_calendar(dict(events=[event]),snapshot)
        self.assertFalse(event['financials']['consensus']['comparable']['operatingProfit'])
        self.assertEqual(event['financials']['history'][1]['operatingProfit'],100)
    def test_non_quarter_and_unverified_currency_do_not_join_consensus(self):
        event=parse_filing(filing(),NOTICE);event['financials']=dict(period='2026.FY',metrics={},history=[])
        snapshot=dict(metadata={},companies=[dict(stockCode='000020',fiscalMonth=12,localCurrencyUnverified=True,marketCap=5000,actuals={},consensus={})])
        enrich_calendar(dict(events=[event]),snapshot);self.assertNotIn('marketCap',event)
        self.assertNotIn('consensus',event['financials'])

if __name__=='__main__':unittest.main()
