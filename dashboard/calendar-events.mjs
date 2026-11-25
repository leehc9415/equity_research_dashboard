// Retain source records; prefer consolidated figures only in the calendar view.
function publishedKey(event){
  if(event.type!=='earnings'||event.status!=='published')return null;
  const period=event.financials?.period||event.period;
  if(!event.corp||!event.date||!period)return null;
  return JSON.stringify([event.corp,event.date,period]);
}

export function preferredCalendarEvents(events){
  events=events.filter(event=>{
    if(event.type!=='earnings')return true;
    const period=event.financials?.period||event.period||'';
    const monthly=period.match(/^20\d{2}\.(\d{2})~(\d{2})$/);
    return !monthly||monthly[1]!==monthly[2];
  });
  const consolidated=new Set(events.filter(e=>e.basis==='연결').map(publishedKey).filter(Boolean));
  return events.filter(event=>{
    const key=publishedKey(event);
    return !key||event.basis==='연결'||!consolidated.has(key);
  });
}

export function calendarDayGroups(events,type){
  const statuses=type==='ir'?['scheduled']:['published','scheduled'];
  return statuses.map(status=>({status,events:events.filter(e=>e.type===type&&e.status===status)})).filter(group=>group.events.length);
}

// Reported actuals are authoritative for every published earnings release.
export function useReportedBasis(event){
  return event.type==='earnings'&&event.status==='published'&&!!event.financials;
}

export function hasBasisDifference(event){
  return Object.values(event.financials?.consensus?.comparable||{}).some(value=>value===false);
}

export function financialHistory(event){
  const history=event.financials?.history||[];
  return hasBasisDifference(event)?history.filter(row=>row.source==='DART'):history;
}

// Growth vs a base period; sign flips get a label instead of a meaningless percentage.
export function growth(value,base){
  if(!Number.isFinite(value)||!Number.isFinite(base)||base===0)return {rate:null,label:''};
  if(base<0)return {rate:null,label:value>=0?'흑자전환':'적자지속'};
  if(value<0)return {rate:null,label:'적자전환'};
  return {rate:(value-base)/base*100,label:''};
}

// 실적 모아보기 행: 화면 표 한 줄. source는 '분기보고서' 또는 '잠정'.
function summaryRow({stock,company,date,basis,receipt,marketCap,metrics,consensus,source},sectors){
  const [sector,subSector]=sectors[stock]||['미분류','미분류'];
  const metric=key=>{const m=metrics?.[key]||{},actual=m.value??null,expected=consensus?.[key]??null,y=growth(actual,m.priorYear),q=growth(actual,m.previous);
    return {actual,expected,yoy:y.rate,yoyLabel:y.label,qoq:q.rate,qoqLabel:q.label,vs:consensusComparison(actual,expected)};};
  return {stock,company,date:date||'',basis,source,url:receipt?'https://dart.fss.or.kr/dsaf001/main.do?rcpNo='+receipt:'',
    marketCap:Number.isFinite(marketCap)?marketCap:null,sector,subSector,
    revenue:metric('revenue'),operatingProfit:metric('operatingProfit'),netIncome:metric('netIncome')};
}

// 분기보고서(quarterly-results.json) 기준 행.
export function reportRows(quarter,sectors={}){
  return (quarter?.companies||[]).map(c=>summaryRow({...c,date:c.filed,source:'분기보고서'},sectors));
}

// 잠정실적 공시 기준 행: 종목당 하나, 연결 우선 후 최신 공시(정정 포함).
export function preliminaryRows(events,period,sectors={}){
  const best=new Map();
  for(const e of events){
    if(e.type!=='earnings'||e.status!=='published'||e.financials?.period!==period||!e.stock)continue;
    const rank=[e.basis==='연결'?1:0,e.date,e.receipt||''].join('|'),prev=best.get(e.stock);
    if(!prev||rank>prev.rank)best.set(e.stock,{rank,event:e});
  }
  return [...best.values()].map(({event:e})=>summaryRow({stock:e.stock,company:e.company,date:e.date,basis:e.basis,receipt:e.receipt,marketCap:e.marketCap,
    metrics:e.financials.metrics,consensus:e.financials.consensus?.metrics,source:'잠정'},sectors));
}

// 잠정실적으로 먼저 채우고, 분기보고서가 나온 종목은 분기보고서 수치로 교체.
export function seasonRows(quarter,events,period,sectors={}){
  const rows=new Map(preliminaryRows(events,period,sectors).map(r=>[r.stock,r]));
  for(const r of reportRows(quarter,sectors))rows.set(r.stock,r);
  return [...rows.values()];
}

export function consensusComparison(actual,expected){
  if(!Number.isFinite(actual)||!Number.isFinite(expected))return null;
  if(expected===0)return {text:actual>0?'예상 0→흑자':actual<0?'예상 0→적자':'예상 부합',positive:actual>=0,percent:null,description:'예상치 0으로 비율 계산 불가'};
  const percent=(actual-expected)/Math.abs(expected)*100;
  const description=expected<0?(actual>=0?'예상 적자→흑자':actual>expected?'예상보다 적자 축소':actual<expected?'예상보다 적자 확대':'예상 부합'):'';
  return {text:`${percent>=0?'+':''}${percent.toFixed(1)}%`,positive:percent>=0,percent,description};
}
