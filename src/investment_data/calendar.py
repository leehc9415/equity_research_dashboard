"""DART calendar collection. No credentials or Excel refresh runs in the browser."""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen
from zipfile import ZipFile
import copy, hashlib, json, os, re, tempfile, time

KST=timezone(timedelta(hours=9))
class CollectionError(RuntimeError): pass

class Rows(HTMLParser):
    def __init__(self):
        super().__init__();self.rows=[];self.row=None;self.cell=None
    def handle_starttag(self,tag,attrs):
        if tag=='tr':self.row=[]
        elif tag in ('td','th') and self.row is not None:self.cell=[]
        elif tag=='br' and self.cell is not None:self.cell.append(' ')
    def handle_data(self,data):
        if self.cell is not None:self.cell.append(data)
    def handle_endtag(self,tag):
        if tag in ('td','th') and self.row is not None and self.cell is not None:
            self.row.append(re.sub(r'\s+',' ',''.join(self.cell)).strip());self.cell=None
        elif tag=='tr' and self.row is not None:self.rows.append(self.row);self.row=None

def dates(text):return re.findall(r'20\d{2}-\d{2}-\d{2}',text)
def field(rows,labels):
    return next((' '.join(row[1:]) for row in rows if row and any(label in row[0] for label in labels)),'')
def number(text):
    text=text.replace(',','').strip()
    return float(text) if re.fullmatch(r'[-+]?\d+(?:\.\d+)?',text) else None
def period(ds):
    if len(ds)<2:return None
    a,b=map(date.fromisoformat,[ds[0],ds[-1]])
    months=(b.year-a.year)*12+b.month-a.month+1
    return f'{b.year}.{(b.month-1)//3+1}Q' if months==3 else f'{b.year}.FY' if months==12 else f'{b.year}.{a.month:02d}~{b.month:02d}'
def classify(title):
    title=re.sub(r'\s','',title)
    if '자회사' in title:return None
    if '연결재무제표기준영업(잠정)실적' in title:return 'consolidated'
    if '영업(잠정)실적' in title:return 'earnings'
    if '결산실적공시예고' in title:return 'notice'
    if re.search(r'기업설명회\(IR\)개최(?!결과)',title):return 'ir'
    return None

class DartClient:
    def __init__(self,key,cache=None):
        self.key=key;self.cache=Path(cache) if cache else None
        if self.cache:self.cache.mkdir(parents=True,exist_ok=True)
    def request(self,endpoint,params):
        url='https://opendart.fss.or.kr/api/'+endpoint+'?'+urlencode(dict(crtfc_key=self.key,**params))
        for attempt in range(3):
            try:
                with urlopen(url,timeout=40) as response:return response.read()
            except Exception:
                if attempt==2:raise CollectionError('DART connection failed; existing calendar preserved') from None
                time.sleep(attempt+1)
    def filings(self,start,end):
        result=[]
        for page in range(1,201):
            body=json.loads(self.request('list.json',dict(bgn_de=start.strftime('%Y%m%d'),end_de=end.strftime('%Y%m%d'),pblntf_ty='I',last_reprt_at='N',page_count=100,page_no=page,sort='date',sort_mth='asc')))
            if body.get('status')=='013':return result
            if body.get('status')!='000':raise CollectionError('DART list status '+str(body.get('status')))
            result.extend(body.get('list',[]))
            if page>=int(body['total_page']):return result
        raise CollectionError('DART pagination limit; incomplete result was not published')
    def document(self,receipt):
        cached=self.cache/(receipt+'.html') if self.cache else None
        if cached and cached.exists():return cached.read_text(encoding='utf-8')
        raw=self.request('document.xml',dict(rcept_no=receipt))
        if not raw.startswith(b'PK'):raise CollectionError('DART document unavailable: '+receipt)
        with ZipFile(BytesIO(raw)) as archive:
            entries=[e for e in archive.infolist() if e.filename.lower().endswith(('.xml','.htm','.html'))]
            if not entries or sum(e.file_size for e in entries)>20_000_000:raise CollectionError('Unsupported DART document: '+receipt)
            content=archive.read(entries[0])
        for encoding in ('utf-8-sig','euc-kr'):
            try:
                decoded=content.decode(encoding)
                if cached:cached.write_text(decoded,encoding='utf-8')
                return decoded
            except UnicodeDecodeError:pass
        raise CollectionError('DART document encoding: '+receipt)

def financials(rows,basis):
    unit=' '.join(cell for row in rows for cell in row if re.search(r'단위\s*[:：]',cell))
    factor=.01 if '백만원' in unit else .00001 if '천원' in unit else 1 if '억원' in unit else 10000 if '조원' in unit else 1e-8 if re.search(r'단위\s*[:：]\s*원',unit) else None
    if factor is None:raise CollectionError('Unknown earnings unit')
    periods={row[0]:period(dates(' '.join(row[1:]))) for row in rows if row and row[0] in ('당기실적','전기실적','전년동기실적')}
    if not periods.get('당기실적'):raise CollectionError('Earnings period not found')
    metrics={}
    for row in rows:
        if not row:continue
        key={'매출액':'revenue','영업이익':'operatingProfit','당기순이익':'netIncome'}.get(re.sub(r'\s','',row[0]))
        if not key or key in metrics or len(row)<7 or row[1] not in ('당해실적','당기실적'):continue
        values=row[2:]
        if len(values)==7:cur,prev,qoq,qt,prior,yoy,yt=values
        elif len(values)==5:cur,prev,qoq,prior,yoy=values;qt=yt=''
        else:raise CollectionError('Unsupported earnings table')
        amount=lambda v:round(number(v)*factor,5) if number(v) is not None else None
        metrics[key]=dict(value=amount(cur),previous=amount(prev),priorYear=amount(prior),qoq=number(qoq),yoy=number(yoy),qoqLabel=qt if qt not in ('','-') else '',yoyLabel=yt if yt not in ('','-') else '')
    if not metrics:raise CollectionError('Earnings figures not found')
    history=[dict(period=periods[name],source='DART',**{k:m[value] for k,m in metrics.items()}) for name,value in [('당기실적','value'),('전기실적','previous'),('전년동기실적','priorYear')] if periods.get(name)]
    return dict(unit='억원',sourceUnit=unit,period=periods['당기실적'],metrics=metrics,history=history,consensus=None)

def parse_filing(filing,content):
    kind=classify(filing['report_nm']);p=Rows();p.feed(content);rows=p.rows
    received=datetime.strptime(filing['rcept_dt'],'%Y%m%d').date().isoformat()
    event=dict(company=filing['corp_name'],stock=filing['stock_code'],corp=filing['corp_code'],market='유가증권' if filing['corp_cls']=='Y' else '코스닥',receipt=filing['rcept_no'],url='https://dart.fss.or.kr/dsaf001/main.do?rcpNo='+filing['rcept_no'],report=filing['report_nm'].strip(),received=filing['rcept_dt'],date=received,type='ir' if kind=='ir' else 'earnings',status='scheduled' if kind in ('ir','notice') else 'published',isCorrection='정정' in filing['report_nm'])
    original=field(rows,['공시서류제출일'])
    ds=dates(original)
    if not ds:
        m=re.search(r'(20\d{2})\s*년\s*(\d+)\s*월\s*(\d+)\s*일',original)
        if m:ds=[f'{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}']
    if ds:event['originalReceived']=ds[0].replace('-','')
    if kind=='notice':
        ds=dates(field(rows,['공시예정일','공시 예정일']))
        if not ds:raise CollectionError('Announcement date not found: '+event['receipt'])
        event['date']=ds[0]
        for i,row in enumerate(rows):
            if row and '결산대상기간' in row[0]:
                values=dates(' '.join(' '.join(r) for r in rows[i:i+3]));event['period']=period(values[:2]);break
    elif kind=='ir':
        for i,row in enumerate(rows):
            if row and re.match(r'1\.?\s*일시',row[0]):
                candidate=next((' '.join(r) for r in rows[i:i+5] if dates(' '.join(r))),None)
                if candidate:
                    ds=dates(candidate);event['date']=ds[0];event['endDate']=ds[-1]
                    event['time']='–'.join(re.findall(r'\b\d{2}:\d{2}\b',candidate))
                    headers=' '.join(' '.join(r) for r in rows[i:i+3])
                    if len(ds)>1 and not ('시작일' in headers and '종료일' in headers):
                        explicit=[]
                        for day,clock in re.findall(r'(20\d{2}-\d{2}-\d{2})\s*(\d{2}:\d{2})?',candidate):
                            if day not in [e['date'] for e in explicit]:explicit.append(dict(date=day,time=clock))
                        event['occurrences']=sorted(explicit,key=lambda e:e['date'])
                        event['date']=min(ds);event['endDate']=max(ds)
                    break
        else:raise CollectionError('IR date not found: '+event['receipt'])
        if not event.get('endDate'):raise CollectionError('IR date not found: '+event['receipt'])
        event['purpose']=field(rows,['개최목적','실시목적']);event['place']=field(rows,['2. 장소'])
    else:
        event['basis']='연결' if kind=='consolidated' else '별도' if '별도' in ' '.join(' '.join(r) for r in rows) else '개별 등'
        event['financials']=financials(rows,event['basis']);event['period']=event['financials']['period']
        if event.get('originalReceived'):event['date']=datetime.strptime(event['originalReceived'],'%Y%m%d').date().isoformat()
    return event

def validate(data):
    if data.get('schemaVersion')!=1 or not isinstance(data.get('events'),list):raise CollectionError('Invalid calendar schema')
    ids=set()
    for e in data['events']:
        if data.get('publicDataSources')==['OpenDART']:
            f=e.get('financials',{})
            if 'quantiwise' in data or 'marketCap' in e or f.get('consensus') or any(h.get('source')=='Quantiwise' for h in f.get('history',[])):
                raise CollectionError('Private enrichment is not allowed in the DART-only public bundle')
        key=(e['receipt'],e['date'])
        if key in ids:raise CollectionError('Duplicate calendar receipt/date')
        ids.add(key);date.fromisoformat(e['date'])
        if not re.fullmatch(r'\d{8}',e['corp']) or not re.fullmatch(r'\d{14}',e['receipt']):raise CollectionError('Invalid DART identifier')
        if e['type'] not in ('earnings','ir') or e['status'] not in ('published','scheduled'):raise CollectionError('Invalid event category')
        parsed=urlparse(e['url'])
        if parsed.scheme!='https' or parsed.hostname!='dart.fss.or.kr':raise CollectionError('Unsafe filing link')

def merge(events,new):
    same=[e for e in events if e['corp']==new['corp'] and e['type']==new['type']]
    match=None
    if new['status']=='published':
        match=next((e for e in same if e['status']=='published' and e['date']==new['date'] and e.get('financials',{}).get('period')==new['financials']['period'] and e.get('basis')==new['basis']),None)
    elif new['type']=='earnings':
        match=next((e for e in same if e['status']=='scheduled' and ((new.get('period') and e.get('period')==new['period']) or e['date']==new['date'])),None)
    if new.get('originalReceived') and not match:
        candidates=[e for e in same if e.get('received')==new['originalReceived']]
        if len({e['receipt'] for e in candidates})==1:match=candidates[0]
        elif len(candidates)>1:raise CollectionError('Ambiguous correction requires review')
    reference=next((e for e in events if e['stock']==new['stock'] and e.get('marketCap') is not None),{})
    for key in ('marketCap','marketCapPeriod'):
        if key in reference:new[key]=reference[key]
    if match:
        new['sources']=match.get('sources',[dict(receipt=match['receipt'],url=match['url'])])+[dict(receipt=new['receipt'],url=new['url'])]
        if new['status']=='published':
            old=match.get('financials',{});fresh=new['financials']
            fresh['consensus']=old.get('consensus')
            history={h['period']:h for h in old.get('history',[])}
            history.update({h['period']:h for h in fresh['history']})
            fresh['history']=sorted(history.values(),key=lambda h:h['period'],reverse=True)[:5]
            fresh['historyHasQuantiwise']=old.get('historyHasQuantiwise',False)
        if new['type']=='ir':events[:]=[e for e in events if e['receipt']!=match['receipt']]
        else:events.remove(match)
    elif new['status']=='published':
        reference=next((e for e in same if e.get('financials',{}).get('period')==new['financials']['period'] and e.get('financials',{}).get('consensus')),None)
        if reference:new['financials']['consensus']=reference['financials']['consensus']
    if new['status']=='published':
        events[:]=[e for e in events if not(e['corp']==new['corp'] and e['type']=='earnings' and e['status']=='scheduled' and ((e.get('period') and e['period']==new['financials']['period']) or e['date']==new['date']))]
    if new['type']=='earnings' and new['status']=='scheduled' and new.get('period') and any(e['corp']==new['corp'] and e['status']=='published' and e.get('financials',{}).get('period')==new['period'] for e in events):return
    if new['type']=='ir':
        start=date.fromisoformat(new['date']);end=date.fromisoformat(new.get('endDate',new['date']))
        if not 0<=(end-start).days<=366:raise CollectionError('Invalid IR date range')
        if new.get('occurrences'):
            for occurrence in new['occurrences']:events.append(dict(new,date=occurrence['date'],time=occurrence['time']))
        else:
            for offset in range((end-start).days+1):events.append(dict(new,date=(start+timedelta(days=offset)).isoformat()))
    else:events.append(new)

def collect(current,client,today=None):
    today=today or datetime.now(KST).date();data=copy.deepcopy(current)
    last=current.get('collection',{}).get('through')
    start=max(today-timedelta(days=80),date.fromisoformat(last)-timedelta(days=7)) if last else today-timedelta(days=31)
    filings=client.filings(start,today)
    known=set(data.get('processedReceipts',[]))|{e['receipt'] for e in data['events']}
    known.update(s['receipt'] for e in data['events'] for s in e.get('sources',[]))
    added=0
    for filing in sorted(filings,key=lambda f:f['rcept_no']):
        if filing['corp_cls'] not in ('Y','K') or not classify(filing['report_nm']) or filing['rcept_no'] in known:continue
        try:event=parse_filing(filing,client.document(filing['rcept_no']))
        except CollectionError as error:raise CollectionError(str(error)+' [receipt '+filing['rcept_no']+']') from None
        merge(data['events'],event);known.add(event['receipt']);added+=1
    validate(data)
    if added or not current.get('collection'):
        data.update(asOf=today.isoformat(),generatedAt=datetime.now(timezone.utc).isoformat(),sample=False)
        data['coverage']=start.isoformat()+'~'+today.isoformat()+' 접수 공시'
        data['collection']=dict(source='OpenDART',fromDate=start.isoformat(),through=today.isoformat(),complete=True,newFilings=added)
        data['processedReceipts']=sorted(known)
        data['events'].sort(key=lambda e:(e['date'],e['company'],e['receipt']))
    return data,added

def atomic_write(path,data):
    validate(data);path=Path(path);payload=json.dumps(data,ensure_ascii=False,separators=(',',':'))+'\n'
    handle,temp=tempfile.mkstemp(prefix='.calendar-',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(handle,'w',encoding='utf-8',newline='\n') as stream:stream.write(payload);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)
