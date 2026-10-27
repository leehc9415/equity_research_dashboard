let cachedData;
let dataPromise;
let remembered;
export async function mountCalendar(container){
  container.innerHTML='<div class="loading">공시 캘린더를 불러오는 중입니다…</div>';
  try{
    if(!dataPromise)dataPromise=fetch('data/calendar-data.json').then(async response=>{if(!response.ok)throw new Error('캘린더 데이터 '+response.status);const data=await response.json();if(data.schemaVersion!==1||!Array.isArray(data.events))throw new Error('캘린더 데이터 형식 오류');cachedData=data;return data;}).catch(error=>{dataPromise=null;throw error;});
    const dataset=cachedData||await dataPromise;
    if(!container.isConnected||!document.querySelector('[data-section="calendar"]')?.classList.contains('active'))return;
    container.innerHTML='<div class="calendar-page-head"><div class="eyebrow">EVENT CALENDAR</div><h1>캘린더</h1><p>기업 실적 발표와 IR 일정을 확인합니다.</p><div class="calendar-status"></div></div>'+"<div id=\"dart-calendar-sample\" aria-label=\"투자 대시보드 일정 캘린더 샘플\">\n<div class=\"dc-header\">\n    <div class=\"dc-month-control\"><button type=\"button\" class=\"dc-arrow\" data-action=\"prev\" aria-label=\"이전 달\">‹</button><button type=\"button\" class=\"dc-today-button\" data-action=\"today\">오늘</button></div>\n    <div class=\"dc-heading\"><div class=\"dc-month\"></div><div class=\"dc-total\" aria-live=\"polite\"></div></div>\n    <div class=\"dc-month-control\"><button type=\"button\" class=\"dc-arrow\" data-action=\"next\" aria-label=\"다음 달\">›</button></div>\n  </div>\n  <div class=\"dc-toolbar\">\n    <div class=\"dc-tabs\" role=\"tablist\" aria-label=\"일정 종류\">\n      <button type=\"button\" id=\"dc-earnings-tab\" class=\"dc-tab\" role=\"tab\" aria-selected=\"true\" aria-controls=\"dc-content\" data-tab=\"earnings\">실적발표</button>\n      <button type=\"button\" id=\"dc-ir-tab\" class=\"dc-tab\" role=\"tab\" aria-selected=\"false\" aria-controls=\"dc-content\" data-tab=\"ir\">IR일정</button>\n    </div>\n    <div class=\"dc-legend\"></div>\n  </div>\n  <div id=\"dc-content\" role=\"tabpanel\" aria-labelledby=\"dc-earnings-tab\">\n    <div class=\"dc-layout\">\n      <div class=\"dc-calendar\"><div class=\"dc-weekdays\" aria-hidden=\"true\"><span>일</span><span>월</span><span>화</span><span>수</span><span>목</span><span>금</span><span>토</span></div><div class=\"dc-grid\" role=\"group\" aria-label=\"월간 일정 날짜 선택\"></div></div>\n      <aside class=\"dc-sidebar\" aria-label=\"선택한 날짜의 기업 목록\"><div class=\"dc-sidebar-header\"><span class=\"dc-selected-date\"><span class=\"dc-date-label\"></span></span><span class=\"dc-selected-count\"></span></div><div class=\"dc-details\" aria-live=\"polite\"></div></aside>\n    </div>\n  </div>\n  <div class=\"dc-footer\"><span>실제 공시 표본 · 과거 실적·컨센서스 Quantiwise</span><span>QoQ 전분기 · YoY 전년동기 · 엑셀 갱신 10/28 02:41</span></div>\n</div>\n";
    const status=container.querySelector('.calendar-status');
    status.textContent=dataset.sample?'실제 공시 표본을 연결한 시범 화면입니다. 수집 범위에 포함되지 않은 일정은 표시되지 않습니다.':`DART 수집 기준 ${dataset.asOf}${dataset.quantiwise?' · Quantiwise 보강값은 별도 저장 기준':' · 공개 공시 데이터'}`;
    initializeCalendar(dataset);
  }catch(error){container.innerHTML='<div class="card card-pad"><h2>캘린더를 불러오지 못했습니다</h2><p>잠시 후 다시 시도해 주세요.</p><button id="calendar-retry">다시 시도</button></div>';container.querySelector('#calendar-retry').onclick=()=>mountCalendar(container);}
}
function initializeCalendar(dataset){

  const root=document.getElementById('dart-calendar-sample');
  const events=dataset.events;
  const hasMarketCap=events.some(e=>Number.isFinite(e.marketCap));
  const refreshStamp=dataset.quantiwise?.savedRefreshAt?.slice(5,16).replace('-','/').replace('T',' ')||'';
  const capPeriod=dataset.quantiwise?.marketCapColumn?.period;
  const capPeriodLabel=capPeriod&&/^\d{6}$/.test(capPeriod)?`${capPeriod.slice(0,4)}.${capPeriod.slice(4)}`:'';
  const today=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul'}).format(new Date());
  const months=[...new Set([today.slice(0,7),...dataset.events.map(e=>e.date.slice(0,7))])].sort();
  const firstMonth=months[0],lastMonth=months.at(-1);
  const allowedMonth=month=>month>=firstMonth&&month<=lastMonth;
  const settings={visibleCompanies:3,panelWidth:320};
  let state=remembered||{month:today.slice(0,7),selected:dataset.events.filter(e=>e.status==='published').map(e=>e.date).sort().at(-1)||today,tab:'earnings',page:0,sort:hasMarketCap?'marketCap':'operatingProfit',expanded:'20261027800226'};
  state.month=state.selected.slice(0,7);
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const iso=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  const localDate=s=>new Date(s+'T12:00:00');
  const dayEvents=day=>events.filter(e=>e.date===day && e.type===state.tab).sort((a,b)=>a.company.localeCompare(b.company,'ko'));
  const shortPeriod=s=>(s||'').replace(/^20/,'');
  const amount=n=>{
    if(n===null||n===undefined||!Number.isFinite(n))return '—';
    const value=Math.round(Math.abs(n)),sign=n<0?'−':'';
    if(value>=10000){const tail=value%10000;return `${sign}${Math.floor(value/10000)}조${tail?tail.toLocaleString('ko-KR'):''}억`;}
    return sign+Math.abs(n).toLocaleString('ko-KR',{maximumFractionDigits:Math.abs(n)<100?1:0})+'억';
  };
  function growth(m){
    return [['qoq','QoQ'],['yoy','YoY']].map(([key,label])=>{
      const rate=m?.[key],turn=m?.[key+'Label'];
      let text='—',direction='';
      if(turn){text=turn;direction=turn==='흑자전환'?'dc-yoy-up':turn==='적자전환'?'dc-yoy-down':'';}
      else if(Number.isFinite(rate)){text=`${rate>0?'+':''}${rate.toFixed(1)}%`;direction=rate>0?'dc-yoy-up':rate<0?'dc-yoy-down':'';}
      return `<span class="dc-yoy ${direction}" data-growth="${key}"><span class="dc-growth-label">${label}</span><span>${esc(text)}</span></span>`;
    }).join('');
  }
  function resultRow(e){
    const f=e.financials;
    if(!f)return `<div class="dc-company"><span class="dc-company-name">${esc(e.company)}</span></div>`;
    const keys=[['revenue','매출'],['operatingProfit','영업이익'],['netIncome','순이익']];
    const metrics=keys.map(([key,label])=>`<span class="dc-metric"><span class="dc-metric-label">${label}</span><span class="dc-metric-value">${amount(f.metrics[key]?.value)}</span>${growth(f.metrics[key])}</span>`).join('');
    const rows=f.history.map(row=>`<tr><td data-tooltip="${esc(row.source||'DART')}">${esc(row.period)}</td><td>${amount(row.revenue)}</td><td>${amount(row.operatingProfit)}</td></tr>`).join('');
    const consensus=f.consensus;
    const expected=consensus?.metrics.operatingProfit;
    const actual=f.metrics.operatingProfit?.value;
    let surprise='';
    if(expected!==null&&expected!==undefined&&actual!==null&&actual!==undefined&&consensus?.comparable.operatingProfit!==false){
      let text='',positive=false;
      if(expected>0){const pct=(actual-expected)/expected*100;text=`${pct>=0?'+':''}${pct.toFixed(1)}%`;positive=pct>=0;}
      else if(expected<0){text=actual>=0?'예상 적자→흑자':actual>expected?'예상보다 적자 축소':actual<expected?'예상보다 적자 확대':'예상 부합';positive=actual>=expected;}
      else{text=actual>0?'예상 0→흑자':actual<0?'예상 0→적자':'예상 부합';positive=actual>=0;}
      surprise=`<span class="dc-surprise ${positive?'positive':''}" data-tooltip="Quantiwise 저장 컨센서스 기준 · 예상 영업이익 ${amount(expected)} · ${esc(consensus.baseDateSetting||'')}">예상比 영익 ${esc(text)}</span>`;
    }
    const estimates=consensus?`<div class="dc-consensus"><div class="dc-consensus-label">${esc(f.period)} 컨센서스</div><div class="dc-metrics">${keys.map(([key,label])=>`<span class="dc-metric"><span class="dc-metric-label">${label}</span><span class="dc-metric-value">${amount(consensus.metrics[key])}</span></span>`).join('')}</div><div class="dc-result-foot" data-tooltip="원본 설정 ${esc(consensus.baseDateSetting||'')} · 저장값을 그대로 사용">Quantiwise · 엑셀 갱신 ${esc(refreshStamp)}</div></div>`:'';
    return `<details class="dc-result" data-receipt="${esc(e.receipt)}" ${state.expanded===e.receipt?'open':''}><summary class="dc-result-summary"><span class="dc-result-top"><span class="dc-result-name" ${Number.isFinite(e.marketCap)?`data-tooltip="시가총액 ${amount(e.marketCap)} · 원본 기준기간 ${esc(capPeriodLabel)}"`:''}>${esc(e.company)}</span><span class="dc-period">${esc(shortPeriod(f.period))}</span>${surprise}<span class="dc-chevron" aria-hidden="true"></span></span><span class="dc-metrics">${metrics}</span></summary><div class="dc-result-expanded"><div class="dc-result-caption">${f.historyHasQuantiwise?'최근 분기 실적':'공시 내 비교 실적'}</div><table class="dc-result-table" aria-label="${esc(e.company)} 비교기간별 실적"><thead><tr><th>기간</th><th>매출액</th><th>영업이익</th></tr></thead><tbody>${rows}</tbody></table><div class="dc-result-foot">${esc(e.basis)} · 잠정실적 · 단위 억원</div>${estimates}</div></details>`;
  }
  function persist(){remembered={...state};}
  function draw(){
    root.style.setProperty('--dc-panel-width',settings.panelWidth+'px');
    root.querySelector('.dc-footer span:first-child').textContent=dataset.quantiwise?'DART 공시 · 과거 실적·컨센서스 Quantiwise':'출처 OpenDART · 실적발표 / IR';
    root.querySelector('.dc-footer span:last-child').textContent=`QoQ 전분기 · YoY 전년동기${refreshStamp?' · 엑셀 갱신 '+refreshStamp:''}`;
    const [year,month]=state.month.split('-').map(Number);
    root.querySelector('.dc-month').textContent=`${year}년 ${month}월`;
    const monthEvents=events.filter(e=>e.date.startsWith(state.month) && e.type===state.tab);
    root.querySelector('.dc-total').textContent=`${dataset.sample?'표본 ':''}일정 ${monthEvents.length}건`;
    root.querySelector('[data-action="prev"]').disabled=state.month===firstMonth;
    root.querySelector('[data-action="next"]').disabled=state.month===lastMonth;
    root.querySelectorAll('[data-tab]').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.tab===state.tab)));
    root.querySelector('#dc-content').setAttribute('aria-labelledby',state.tab==='earnings'?'dc-earnings-tab':'dc-ir-tab');
    root.querySelector('.dc-legend').innerHTML=state.tab==='ir'?'<span class="dc-key"><span class="dc-dot ir"></span>IR 일정</span>':'<span class="dc-key"><span class="dc-dot"></span>발표 예정</span><span class="dc-key"><span class="dc-dot published"></span>공시 발표</span>';
    const first=new Date(year,month-1,1,12);const count=new Date(year,month,0).getDate();const cells=Math.ceil((first.getDay()+count)/7)*7;
    const grid=[];
    for(let i=0;i<cells;i++){
      const d=new Date(year,month-1,i-first.getDay()+1,12),key=iso(d),items=dayEvents(key),outside=d.getMonth()!==month-1;
      const published=state.tab==='earnings'&&items.length>0&&items.every(e=>e.status==='published');
      const cls=state.tab==='ir'?'ir':published?'published':'';
      const label=`${d.getMonth()+1}월 ${d.getDate()}일, ${state.tab==='ir'?'IR':'실적'} ${items.length}건`;
      let content='';
      if(items.length){
        if(published)content+=`<span class="dc-published-count">실적 ${items.length}곳</span>`;
        const limit=published?2:settings.visibleCompanies;
        content+=items.slice(0,limit).map(e=>`<span class="dc-event ${cls}" data-tooltip="${esc(e.company)}">${esc(e.company)}</span>`).join('');
        if(items.length>limit)content+=`<span class="dc-more">+${items.length-limit}건</span>`;
        content+=`<span class="dc-tiny-count ${cls}">${items.length}건</span>`;
      }
      grid.push(`<button type="button" class="dc-day ${outside?'dc-outside':''}" data-date="${key}" aria-pressed="${state.selected===key}" aria-label="${esc(label)}"><span class="dc-date ${key===today?'dc-today':''}">${d.getDate()}</span>${content}</button>`);
    }
    root.querySelector('.dc-grid').innerHTML=grid.join('');
    const d=localDate(state.selected),items=dayEvents(state.selected);
    root.querySelector('.dc-date-label').textContent=`${d.getMonth()+1}월 ${d.getDate()}일 (${'일월화수목금토'[d.getDay()]})`;
    root.querySelector('.dc-selected-count').textContent=items.length+'건';
    const details=root.querySelector('.dc-details');
    if(!items.length){details.innerHTML='<div class="dc-empty"><i data-lucide="calendar" aria-hidden="true"></i>이 날짜에 수집된 일정이 없습니다.</div>';}
    else if(state.tab==='ir'){
      details.innerHTML=`<section class="dc-group ir"><div class="dc-group-head"><span class="dc-key"><span class="dc-dot ir"></span>IR 일정</span><span>${items.length}건</span></div>${items.map(e=>`<a class="dc-ir-row" href="${esc(e.url)}" target="_blank" rel="noopener noreferrer" aria-label="${esc(e.company)} IR 공시 원문 새 창"><div class="dc-ir-top"><span class="dc-company-name">${esc(e.company)}</span>${e.time&&e.time!=='시간 미공개'?`<span class="dc-ir-time">${esc(e.time)}</span>`:''}</div><span class="dc-company-meta">${esc(e.stock)} <span class="dc-market">${esc(e.market)}</span></span><div class="dc-ir-purpose">${esc(e.purpose||'기업설명회 개최')}</div></a>`).join('')}</section>`;
    }else{
      details.innerHTML=['scheduled','published'].map(status=>{
        const list=items.filter(e=>e.status===status);if(!list.length)return '';
        if(status==='published'){
          if(state.sort==='operatingProfit')list.sort((a,b)=>(b.financials?.metrics.operatingProfit?.value??-Infinity)-(a.financials?.metrics.operatingProfit?.value??-Infinity));
          if(state.sort==='marketCap')list.sort((a,b)=>((Number.isFinite(b.marketCap)?b.marketCap:-Infinity)-(Number.isFinite(a.marketCap)?a.marketCap:-Infinity))||a.company.localeCompare(b.company,'ko'));
          const pages=Math.ceil(list.length/5);state.page=Math.min(Math.max(Number(state.page)||0,0),pages-1);
          const visible=list.slice(state.page*5,state.page*5+5);
          return `<div class="dc-earnings-controls"><span data-tooltip="${state.sort==='marketCap'?'원본 S열의 시가총액 기준기간 '+esc(capPeriodLabel):'QoQ 전분기 대비 / YoY 전년동기 대비'}">잠정 실적 ${list.length}곳${state.sort==='marketCap'&&capPeriodLabel?' · 시총 '+esc(capPeriodLabel):' · QoQ / YoY'}</span><select class="dc-sort" aria-label="발표 실적 정렬"><option value="marketCap" ${state.sort==='marketCap'?'selected':''}>시가총액순</option><option value="operatingProfit" ${state.sort==='operatingProfit'?'selected':''}>영업이익순</option><option value="company" ${state.sort==='company'?'selected':''}>가나다순</option></select></div><section class="dc-group published">${visible.map(resultRow).join('')}</section>${pages>1?`<div class="dc-pagination"><button type="button" class="dc-page-button" data-page="prev" ${state.page===0?'disabled':''}>이전</button><span>${state.page*5+1}–${Math.min((state.page+1)*5,list.length)} / ${list.length}곳</span><button type="button" class="dc-page-button" data-page="next" ${state.page===pages-1?'disabled':''}>다음</button></div>`:''}`;
        }
        return `<section class="dc-group"><div class="dc-group-head"><span class="dc-key"><span class="dc-dot"></span>실적 발표 예정</span><span>${list.length}건</span></div><div class="dc-company-grid">${list.map(e=>`<div class="dc-company"><span class="dc-company-name">${esc(e.company)}</span><span class="dc-company-meta"><span>${esc(e.stock)}</span><span class="dc-market">${esc(e.market)}</span></span></div>`).join('')}</div></section>`;
      }).join('');
    }

    const capOption=root.querySelector('.dc-sort option[value="marketCap"]');
    if(capOption)capOption.disabled=!hasMarketCap;
  }
  root.addEventListener('click',event=>{
    const summary=event.target.closest('.dc-result-summary');
    if(summary){event.preventDefault();const detail=summary.parentElement;detail.open=!detail.open;root.querySelectorAll('.dc-result').forEach(other=>{if(other!==detail)other.open=false;});state.expanded=detail.open?detail.dataset.receipt:null;persist();return;}
    const day=event.target.closest('[data-date]');
    if(day){const month=day.dataset.date.slice(0,7);if(!allowedMonth(month))return;state.selected=day.dataset.date;state.month=month;state.page=0;draw();persist();return;}
    const tab=event.target.closest('[data-tab]');if(tab){state.tab=tab.dataset.tab;state.page=0;draw();persist();return;}
    const paging=event.target.closest('[data-page]');if(paging){state.page+=paging.dataset.page==='next'?1:-1;draw();persist();return;}
    const action=event.target.closest('[data-action]')?.dataset.action;
    if(action==='today'){state.month=today.slice(0,7);state.selected=today;draw();persist();}
    else if(action==='prev'||action==='next'){
      const shift=new Date(state.month+'-01T12:00:00');shift.setMonth(shift.getMonth()+(action==='next'?1:-1));state.month=iso(shift).slice(0,7);const available=events.filter(e=>e.type===state.tab&&e.date.startsWith(state.month)).map(e=>e.date).sort();state.selected=available[0]||state.month+'-01';draw();persist();
    }
  });
  root.addEventListener('change',event=>{if(event.target.matches('.dc-sort')){state.sort=event.target.value;state.page=0;draw();persist();}});
  root.querySelector('.dc-tabs').addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();state.tab=event.key==='Home'?'earnings':event.key==='End'?'ir':state.tab==='ir'?'earnings':'ir';draw();root.querySelector(`[data-tab="${state.tab}"]`).focus();persist();}});
  draw();
}
