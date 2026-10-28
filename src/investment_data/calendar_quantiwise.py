"""Join the saved, approved Quantiwise snapshot on every DART update."""
import copy,re

METRICS=('revenue','operatingProfit','netIncome')

def period_key(label):
    match=re.fullmatch(r'(20\d{2})\.([1-4])Q',label or '')
    return match[1]+f'{int(match[2])*3:02}' if match else None

def period_label(key):return key[:4]+'.'+str(int(key[4:])//3)+'Q'

def enrich_calendar(data,snapshot):
    companies={c['stockCode']:c for c in snapshot['companies']}
    metadata=snapshot['metadata']
    for event in data['events']:
        company=companies.get(event.get('stock'))
        if not company or company.get('localCurrencyUnverified'):continue
        if company.get('marketCap') is not None:
            event['marketCap']=company['marketCap']
            event['marketCapPeriod']=metadata.get('marketCapColumn',{}).get('period')
        financial=event.get('financials')
        if not financial or company.get('fiscalMonth')!=12:continue
        current=period_key(financial.get('period'))
        if not current:continue
        # Rebuild from original DART comparison figures, never from a previous join.
        metrics=financial.get('metrics',{})
        quarter=int(current[4:])//3
        year=int(current[:4])
        prior=f'{year-1}'+current[4:]
        previous=f'{year-1}12' if quarter==1 else f'{year}{(quarter-1)*3:02}'
        dart={key:dict(period=period_label(key),source='DART',**{m:metrics.get(m,{}).get(field) for m in METRICS}) for key,field in ((current,'value'),(previous,'previous'),(prior,'priorYear'))}
        dart={key:row for key,row in dart.items() if any(row[m] is not None for m in METRICS)}
        comparable={m:True for m in METRICS}
        for key,row in dart.items():
            if key==current:continue
            saved=company.get('actuals',{}).get(key,{})
            for metric in METRICS:
                a,b=row.get(metric),saved.get(metric)
                if a is not None and b is not None and abs(a-b)>max(1,abs(a)*.005):comparable[metric]=False
        history={}
        for key,values in company.get('actuals',{}).items():
            if key>=current:continue
            row=dict(period=period_label(key),source='Quantiwise',**{m:values.get(m) if comparable[m] else None for m in METRICS})
            if any(row[m] is not None for m in METRICS):history[key]=row
        # Source figures always take priority over saved reference figures.
        history.update(dart)
        financial['history']=[history[k] for k in sorted(history,reverse=True)[:5]]
        financial['historyHasQuantiwise']=any(h['source']=='Quantiwise' for h in financial['history'])
        expected=company.get('consensus',{}).get(current)
        financial['consensus']=None
        if expected and any(expected.get(m) is not None for m in METRICS):
            base=next((c.get('baseDateSetting') for c in metadata.get('columns',[]) if c['kind']=='consensus' and c['period']==current),None)
            financial['consensus']=dict(period=financial['period'],metrics=copy.deepcopy(expected),savedRefreshAt=metadata.get('savedRefreshAt'),baseDateSetting=base,comparable=comparable,source='Quantiwise')
    data['quantiwise']={k:copy.deepcopy(v) for k,v in metadata.items() if k in ('savedRefreshAt','sourceUnit','displayUnit','unitMultiplier','marketCapColumn','asOfPolicy','publicationApprovedOn')}
    data['publicDataSources']=['OpenDART','Quantiwise']
    return data
