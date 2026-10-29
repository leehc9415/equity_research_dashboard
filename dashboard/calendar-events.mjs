// Retain source records; prefer consolidated figures only in the calendar view.
function publishedKey(event){
  if(event.type!=='earnings'||event.status!=='published')return null;
  const period=event.financials?.period||event.period;
  if(!event.corp||!event.date||!period)return null;
  return JSON.stringify([event.corp,event.date,period]);
}

export function preferredCalendarEvents(events){
  const consolidated=new Set(events.filter(e=>e.basis==='연결').map(publishedKey).filter(Boolean));
  return events.filter(event=>{
    const key=publishedKey(event);
    return !key||event.basis==='연결'||!consolidated.has(key);
  });
}

export function calendarDayGroups(events,type){
  const statuses=type==='ir'?['scheduled']:['scheduled','published'];
  return statuses.map(status=>({status,events:events.filter(e=>e.type===type&&e.status===status)})).filter(group=>group.events.length);
}

// Explicit display policy requested for Samsung SDI's reclassified 2026 Q3.
// Other companies and periods retain the ordinary basis-review requirement.
export function useReportedBasis(event){
  return event.corp==='00126362'&&event.type==='earnings'&&event.status==='published'&&event.basis==='연결'&&event.financials?.period==='2026.3Q';
}
