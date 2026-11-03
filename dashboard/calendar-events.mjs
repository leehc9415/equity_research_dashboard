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

export function consensusComparison(actual,expected){
  if(!Number.isFinite(actual)||!Number.isFinite(expected))return null;
  if(expected===0)return {text:actual>0?'예상 0→흑자':actual<0?'예상 0→적자':'예상 부합',positive:actual>=0,percent:null,description:'예상치 0으로 비율 계산 불가'};
  const percent=(actual-expected)/Math.abs(expected)*100;
  const description=expected<0?(actual>=0?'예상 적자→흑자':actual>expected?'예상보다 적자 축소':actual<expected?'예상보다 적자 확대':'예상 부합'):'';
  return {text:`${percent>=0?'+':''}${percent.toFixed(1)}%`,positive:percent>=0,percent,description};
}
