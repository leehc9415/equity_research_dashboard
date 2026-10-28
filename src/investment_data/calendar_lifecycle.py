"""Reconcile earnings notices with observed results, including existing history."""
import copy,re

def reporting_period(event):
    label=event.get('financials',{}).get('period') or event.get('period')
    if not label:return None
    if re.fullmatch(r'20\d{2}\.[1-4]Q',label):return label
    annual=re.fullmatch(r'(20\d{2})\.FY',label)
    if annual:return annual[1]+'.4Q'
    span=re.fullmatch(r'(20\d{2})\.(\d{2})~(\d{2})',label)
    if span:
        first,last=int(span[2]),int(span[3])
        if 1<=first<=last<=12 and last in (3,6,9,12) and last-first+1 in (3,6,9,12):
            return span[1]+'.'+str(last//3)+'Q'
    return label

def received_day(event):return str(event.get('received') or event.get('receipt','')[:8] or event['date']).replace('-','')[:8]

def reconcile_earnings(events):
    results={}
    for event in events:
        key=reporting_period(event)
        if event['type']=='earnings' and event['status']=='published' and key:
            results.setdefault((event['corp'],key),[]).append(event)
    removed=[]
    for notice in events:
        if notice['type']!='earnings' or notice['status']!='scheduled':continue
        key=reporting_period(notice)
        if not key:continue
        candidates=[e for e in results.get((notice['corp'],key),[]) if received_day(e)>=received_day(notice)]
        if not candidates:continue
        target=max(candidates,key=lambda e:(e.get('basis')=='연결',e['date'],e['receipt']))
        reference={k:copy.deepcopy(notice[k]) for k in ('receipt','url','period','sources') if k in notice}
        reference['scheduledDate']=notice['date']
        history=target.setdefault('announcements',[])
        if not any(item['receipt']==notice['receipt'] for item in history):history.append(reference)
        removed.append(notice)
    if removed:
        receipts={e['receipt'] for e in removed}
        events[:]=[e for e in events if not(e['type']=='earnings' and e['status']=='scheduled' and e['receipt'] in receipts)]
    return len(removed)
