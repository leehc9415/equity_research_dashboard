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
