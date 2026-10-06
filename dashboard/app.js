const app = document.querySelector('#app');
const fmt = new Intl.NumberFormat('ko-KR', {maximumFractionDigits: 1});
const num = v => fmt.format(v ?? 0);
const money = v => v == null ? '—' : Math.abs(v) >= 1e8 ? `${num(v / 1e8)}억 달러` : Math.abs(v) >= 1e4 ? `${num(v / 1e4)}만 달러` : `${num(v)}달러`;
const pct = v => v == null || !Number.isFinite(v) ? '—' : `${v > 0 ? '+' : ''}${(v * 100).toFixed(1)}%`;
const delta = v => v == null || !Number.isFinite(v) ? 'neutral' : v >= 0 ? 'positive' : 'negative';
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const key = item => `${item.l}-${item.c}`;
const label = item => `${item.n} · ${item.c}`;
const industrySourceNote = '2025-04 이전은 2026년 HS10 매핑을 과거 관세청 수출액에 그대로 적용한 계산값입니다. 2025-05 이후는 산업통상자원부 발표값입니다. 과거 계산값은 HS코드 변경분을 보정하지 않았습니다.';
const tooltip = document.createElement('div'); tooltip.className = 'tooltip'; document.body.appendChild(tooltip);
let D, catalog;
const state = {section:'exports', page:'overview', preset:'1Y', start:'', end:'', ranking:'growth', industry:'반도체', sort:'value', desc:true, catalogIndustry:'전체', query:'', selected:null, country:'ALL', productPeriod:'month'};
const chartSpecs = new Map(), chartViews = new Map();
let mapStats = [];
const chartResizeObserver = new ResizeObserver(entries => {
  for (const entry of entries) {
    const node = entry.target, width = Math.max(280, Math.round(entry.contentRect.width));
    if (node.classList.contains('bubble-chart')) {
      if (Number(node.dataset.width) === width) continue;
      node.dataset.width = String(width);
      node.innerHTML = bubbleMap(mapStats, width);
    } else {
      const id = node.dataset.chartId, spec = chartSpecs.get(id);
      if (!spec || spec.width === width) continue;
      spec.width = width;
      node.innerHTML = chartInner(id);
    }
    attachTips(node);
  }
});
function watchChartSizes() {
  chartResizeObserver.disconnect();
  app.querySelectorAll('.zoom-chart,.bubble-chart').forEach(node => chartResizeObserver.observe(node));
}
function scheduleChartLayout() { queueMicrotask(watchChartSizes); }

function showTip(event, html) { tooltip.innerHTML = html; tooltip.classList.add('show'); tooltip.style.left = `${Math.min(event.clientX+12, innerWidth-190)}px`; tooltip.style.top = `${Math.max(event.clientY-48, 6)}px`; }
function hideTip() { tooltip.classList.remove('show'); }
function attachTips(root=document) { root.querySelectorAll('[data-tip]').forEach(node => {node.addEventListener('mousemove', e => showTip(e, node.dataset.tip));node.addEventListener('mouseleave', hideTip);}); }
function head(eyebrow,title,sub,actions='') { return `<div class="page-head"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><p>${sub}</p></div>${actions ? `<div class="head-actions">${actions}</div>` : ''}</div>`; }
function card(title,sub,body,extra='') {return `<section class="card card-pad"><div class="section-head"><div><div class="section-title">${title}</div>${sub ? `<div class="section-sub">${sub}</div>` : ''}</div>${extra}</div>${body}</section>`;}
function kpi(label,value,note,change=null){return `<div class="card kpi"><div class="label">${label}</div><strong>${value}</strong><small class="${delta(change)}">${note}</small></div>`;}
function monthBack(date, count){let [y,m]=date.split('-').map(Number);let d=new Date(Date.UTC(y,m-1-count,1));return `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}`;}
function chartWindow(id){
  const rows=chartSpecs.get(id)?.rows||[],signature=`${rows.length}:${rows[0]?.[0]}:${rows.at(-1)?.[0]}`;
  let view=chartViews.get(id);
  if(!view||view.signature!==signature){view={signature,start:0,count:rows.length};chartViews.set(id,view);}
  view.count=Math.min(view.count,rows.length);
  view.start=Math.max(0,Math.min(view.start,rows.length-view.count));
  return view;
}
function chartInner(id){
  const {rows,options,kind,width}=chartSpecs.get(id),view=chartWindow(id),visible=rows.slice(view.start,view.start+view.count),full=view.count===rows.length;
  const range=visible.length?`${visible[0][0]} — ${visible.at(-1)[0]} · ${visible.length}/${rows.length}개 기간`:'표시할 기간 없음';
  const button=(action,label,disabled,title)=>`<button type="button" data-chart-action="${action}" aria-label="${title}" title="${title}" ${disabled?'disabled':''}>${label}</button>`;
  return `<div class="chart-zoom-toolbar"><span class="chart-range">${range}</span><div class="chart-zoom-buttons">${button('prev','←',view.start===0,'이전 구간')}${button('in','＋',view.count<=Math.min(6,rows.length),'확대')}${button('out','－',full,'축소')}${button('fit','전체',full,'전체 기간 보기')}${button('next','→',view.start+view.count>=rows.length,'다음 구간')}</div></div><div class="chart-wrap${kind==='spark'?' short':''}">${kind==='spark'?sparkChart(visible,{...options,width,limit:Infinity}):lineChart(visible,{...options,width,limit:Infinity})}</div>`;
}
function zoomChart(id,rows,options={},kind='line'){
  chartSpecs.set(id,{rows,options,kind,width:chartSpecs.get(id)?.width});
  return `<div class="zoom-chart" data-chart-id="${esc(id)}">${chartInner(id)}</div>`;
}
function updateZoomChart(id,rows,options={},kind='line'){
  const node=[...app.querySelectorAll('.zoom-chart')].find(x=>x.dataset.chartId===id);
  if(!node)return;
  chartSpecs.set(id,{rows,options,kind,width:chartSpecs.get(id)?.width});
  node.innerHTML=chartInner(id);
  attachTips(node);
}
app.addEventListener('click',event=>{
  const button=event.target.closest('[data-chart-action]');
  if(!button||button.disabled)return;
  const wrapper=button.closest('.zoom-chart'),id=wrapper?.dataset.chartId,spec=chartSpecs.get(id);
  if(!spec)return;
  const view=chartWindow(id),total=spec.rows.length,action=button.dataset.chartAction;
  if(action==='in'){
    const count=Math.max(Math.min(6,total),Math.ceil(view.count*.6));
    view.start=Math.min(total-count,view.start+view.count-count);view.count=count;
  }else if(action==='out'){
    const count=Math.min(total,Math.ceil(view.count/.6));
    view.start=Math.max(0,view.start+view.count-count);view.count=count;
  }else if(action==='fit'){view.start=0;view.count=total;}
  else if(action==='prev')view.start=Math.max(0,view.start-Math.max(1,Math.floor(view.count*.7)));
  else if(action==='next')view.start=Math.min(total-view.count,view.start+Math.max(1,Math.floor(view.count*.7)));
  wrapper.innerHTML=chartInner(id);
  attachTips(wrapper);
});
function getMonthRange(){const start=state.start, end=state.end;return D.summary.map(r=>r[0]).filter(d=>d>=start&&d<=end);}
function sumRange(rows, start, end){return rows.filter(r=>r[0]>=start&&r[0]<=end).reduce((n,r)=>n+r[2],0);}
function industryPeriod(){const start=state.start>D.industryCoverageStart?state.start:D.industryCoverageStart,end=state.end<D.industryCommonAsOf?state.end:D.industryCommonAsOf;return start<=end?{start,end}:null;}
function industryStats(){
 const period=industryPeriod(), newest=D.industryCommonAsOf;if(!period)return [];
 const national=D.summary.filter(r=>r[0]>=period.start&&r[0]<=period.end).reduce((n,r)=>n+r[1],0);
 const values=D.industryNames.map(name=>{
  const series=D.industries.filter(r=>r[1]===name&&r[0]<=newest),selected=series.filter(r=>r[0]>=period.start&&r[0]<=period.end);
  const value=selected.reduce((n,r)=>n+r[2],0),comparable=selected.filter(r=>r[3]!=null&&r[3]>-1);
  const comparableValue=comparable.reduce((n,r)=>n+r[2],0),base=comparable.reduce((n,r)=>n+r[2]/(1+r[3]),0);
  const latest=series.find(r=>r[0]===newest)||series.at(-1);
  return{name,value,base,growth:base?comparableValue/base-1:null,increase:base?comparableValue-base:null,contribution:null,share:national?value/national:null,yoy:latest?.[3]??null,mom:latest?.[4]??null,series};
 });
 const totalIncrease=values.reduce((n,r)=>n+(r.increase||0),0);
 return values.map(r=>({...r,contribution:totalIncrease&&r.increase!=null?r.increase/totalIncrease:null,period}));
}
function niceStep(target){
  if(!Number.isFinite(target)||target<=0)return 1;
  const magnitude=10**Math.floor(Math.log10(target));
  return ([1,2,2.5,5,10].find(x=>x*magnitude>=target)||10)*magnitude;
}
function lineChart(rows,{height=260,width=800,barIndex=1,lineIndex=2,barColor='#6bbfae',lineColor='#ed766e',linePercent=true,lineUnit='YoY',limit=36}={}){
  rows=rows.slice(-limit);if(!rows.length)return `<div class="chart-empty">표시할 데이터가 없습니다.</div>`;
  const W=Math.max(280,Math.round(width||800)),H=height,L=56,R=64,T=27,B=35,iw=W-L-R,ih=H-T-B;
  const largest=Math.max(...rows.map(r=>Number(r[barIndex])||0),1),barStep=niceStep(largest*1.04/5),barMax=Math.ceil(largest*1.04/barStep)*barStep;
  const barScale=barMax>=1e8?1e8:barMax>=1e4?1e4:1,barUnit=barScale===1e8?'억 달러':barScale===1e4?'만 달러':'달러';
  const lineVals=rows.map(r=>r[lineIndex]).filter(v=>v!=null&&Number.isFinite(v));
  const rawMin=Math.min(0,...lineVals),rawMax=Math.max(0,...lineVals),pad=Math.max(linePercent ? 0.03 : 1,(rawMax-rawMin)*.06),lineStep=niceStep(Math.max(pad,(rawMax-rawMin+pad)/4));
  const minLine=rawMin<0?Math.floor((rawMin-pad)/lineStep)*lineStep:0,maxLine=rawMax>0?Math.ceil((rawMax+pad)/lineStep)*lineStep:rawMin<0?0:lineStep;
  const lineY=v=>T+(maxLine-v)/(maxLine-minLine)*ih,barY=v=>T+ih-v/barMax*ih,x=i=>L+(i+.5)*iw/rows.length;
  const barTicks=Array.from({length:Math.round(barMax/barStep)+1},(_,i)=>i*barStep);
  const grid=barTicks.map(v=>`<line x1="${L}" y1="${barY(v)}" x2="${W-R}" y2="${barY(v)}" stroke="#e8eef3"/><text x="${L-8}" y="${barY(v)+3}" text-anchor="end" fill="#8193a4" font-size="10">${num(v/barScale)}</text>`).join('');
  const rightTicks=lineVals.length?Array.from({length:Math.round((maxLine-minLine)/lineStep)+1},(_,i)=>minLine+i*lineStep).map(v=>`<line x1="${W-R}" y1="${lineY(v)}" x2="${W-R+4}" y2="${lineY(v)}" stroke="#8193a4"/><text x="${W-R+7}" y="${lineY(v)+3}" fill="#8193a4" font-size="10">${linePercent?`${num(v*100)}%`:num(v)}</text>`).join(''):'';
  const bw=Math.max(.6,Math.min(44,iw/rows.length*.68));
  const bars=rows.map((r,i)=>{const value=Math.max(0,Number(r[barIndex])||0),y=barY(value);return `<rect x="${x(i)-bw/2}" y="${y}" width="${bw}" height="${T+ih-y}" rx="${Math.min(2,bw/3)}" fill="${barColor}" opacity=".86" data-tip="${esc(r[0])}<br>${money(r[barIndex])}${r[lineIndex]==null?'':`<br>${lineUnit} ${linePercent?pct(r[lineIndex]):num(r[lineIndex])}`}"/>`;}).join('');
  let segments=[],segment=[];rows.forEach((r,i)=>{if(r[lineIndex]==null||!Number.isFinite(r[lineIndex])){if(segment.length)segments.push(segment);segment=[];}else segment.push(`${x(i)},${lineY(r[lineIndex])}`);});if(segment.length)segments.push(segment);
  const paths=segments.map(s=>`<polyline points="${s.join(' ')}" fill="none" stroke="${lineColor}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>`).join('');
  const dots=rows.length>90?'':rows.map((r,i)=>r[lineIndex]==null?'':`<circle cx="${x(i)}" cy="${lineY(r[lineIndex])}" r="3" fill="${lineColor}" data-tip="${esc(r[0])}<br>${lineUnit} ${linePercent?pct(r[lineIndex]):num(r[lineIndex])}"/>`).join('');
  const labelStep=Math.max(1,Math.ceil(rows.length/Math.max(2,Math.floor(iw/80))));
  const labels=rows.map((r,i)=>i===0||i===rows.length-1||i%labelStep===0?`<text x="${x(i)}" y="${H-7}" text-anchor="middle" fill="#8193a4" font-size="10">${r[0].slice(2)}</text>`:'').join('');
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="수출금액 및 ${lineUnit} 추이">${grid}<line x1="${W-R}" y1="${T}" x2="${W-R}" y2="${T+ih}" stroke="#cbd7df"/>${rightTicks}<text x="${L}" y="12" fill="#8193a4" font-size="10">${barUnit}</text><text x="${W-R+7}" y="12" fill="#8193a4" font-size="10">${lineUnit}${linePercent?' (%)':''}</text>${bars}${paths}${dots}${labels}</svg>`;
}
function sparkChart(rows,{height=190,width=800,color='#e86e6b',percent=true,unit='YoY',limit=36}={}){
  rows=rows.slice(-limit);const vals=rows.map(r=>r[1]).filter(v=>v!=null&&Number.isFinite(v));if(!vals.length)return `<div class="chart-empty">표시할 데이터가 없습니다.</div>`;
  const W=Math.max(280,Math.round(width||800)),H=height,L=48,R=20,T=18,B=31,iw=W-L-R,ih=H-T-B;
  const rawMin=Math.min(0,...vals),rawMax=Math.max(0,...vals),step=niceStep(Math.max(percent ? 0.03 : 1,(rawMax-rawMin)*1.08/4));
  const min=rawMin<0?Math.floor(rawMin/step)*step:0,max=rawMax>0?Math.ceil(rawMax*1.05/step)*step:rawMin<0?0:step,y=v=>T+(max-v)/(max-min)*ih;
  const grid=Array.from({length:Math.round((max-min)/step)+1},(_,i)=>min+i*step).map(v=>`<line x1="${L}" y1="${y(v)}" x2="${W-R}" y2="${y(v)}" stroke="${v===0?'#cbd7df':'#edf1f5'}" ${v===0?'stroke-dasharray="4 4"':''}/><text x="${L-7}" y="${y(v)+3}" text-anchor="end" font-size="10" fill="#8193a4">${percent?`${num(v*100)}%`:num(v)}</text>`).join('');
  let segments=[],segment=[];rows.forEach((r,i)=>{if(r[1]==null){if(segment.length)segments.push(segment);segment=[];}else segment.push(`${L+i*iw/Math.max(1,rows.length-1)},${y(r[1])}`);});if(segment.length)segments.push(segment);
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="${unit} 추이">${grid}${segments.map(s=>`<polyline points="${s.join(' ')}" fill="none" stroke="${color}" stroke-width="2.7" stroke-linecap="round" stroke-linejoin="round"/>`).join('')}${rows.map((r,i)=>r[1]==null?'':`<circle cx="${L+i*iw/Math.max(1,rows.length-1)}" cy="${y(r[1])}" r="5" opacity="0" data-tip="${esc(r[0])}<br>${unit} ${percent?pct(r[1]):money(r[1])}"/>`).join('')}<text x="${L}" y="${H-5}" font-size="10" fill="#8193a4">${rows[0][0]}</text><text x="${W-R}" y="${H-5}" text-anchor="end" font-size="10" fill="#8193a4">${rows.at(-1)[0]}</text></svg>`;
}
function renderOverview(){
  const latest=D.summary.at(-1), stats=industryStats(), period=industryPeriod(), months=D.summary.filter(r=>r[0]>=state.start&&r[0]<=state.end);
  const rankField=state.ranking==='growth'?'growth':state.ranking==='increase'?'increase':'contribution';
  const positive=stats.filter(r=>r[rankField]!=null&&r[rankField]>0).sort((a,b)=>b[rankField]-a[rankField]).slice(0,7);const peak=positive[0]?.[rankField]||1;
  const sorted=[...stats].sort((a,b)=>{let va=a[state.sort],vb=b[state.sort];if(state.sort==='name')return (state.desc?-1:1)*String(va).localeCompare(String(vb),'ko');return (state.desc?-1:1)*((va??-Infinity)-(vb??-Infinity));});
  const table=`<div class="table-scroll"><table class="data-table"><thead><tr>${[['name','산업'],['value','기간 수출액'],['share','전체 비중'],['yoy','최신 YoY'],['mom','최신 MoM'],['growth','기간 성장률']].map(([k,n])=>`<th class="sortable" data-sort="${k}">${n} ${state.sort===k?(state.desc?'↓':'↑'):''}</th>`).join('')}</tr></thead><tbody>${sorted.map(r=>`<tr data-industry="${esc(r.name)}"><td class="name">${esc(r.name)}</td><td>${money(r.value)}</td><td>${pct(r.share)}</td><td><span class="heat ${r.yoy>=0?'up':'down'}">${pct(r.yoy)}</span></td><td><span class="heat ${r.mom>=0?'up':'down'}">${pct(r.mom)}</span></td><td class="${delta(r.growth)}">${pct(r.growth)}</td></tr>`).join('')}</tbody></table></div>`;
  const ranking=`<div class="rank-list">${positive.map((r,i)=>`<div class="rank-row"><span class="rank">${String(i+1).padStart(2,'0')}</span><span class="rank-name">${esc(r.name)}</span><span class="bar-track"><span class="bar-fill" style="display:block;width:${Math.max(3,r[rankField]/peak*100)}%"></span></span><strong>${state.ranking==='increase'?money(r.increase):pct(r[rankField])}</strong></div>`).join('')}</div>`;
  const map=bubbleChart(stats);
  app.innerHTML=head('EXPORT OVERVIEW','한국 수출의 지금','전체 흐름에서 성장 산업까지, 하나의 화면에서 살펴봅니다.')+
    `<div class="kpi-grid overview-kpis">${kpi(`${Number(latest[0].slice(5))}월 총수출`,money(latest[1]),`${latest[0]} · 확정치`)}${kpi('YoY',pct(latest[2]),'전년 동월 대비',latest[2])}${kpi('MoM',pct(latest[3]),'전월 대비',latest[3])}</div>`+
    `<div class="section">${card('총수출 추이',`${state.start}—${state.end} · 월별 수출금액과 YoY`,zoomChart('overview',months,{barIndex:1,lineIndex:2,lineUnit:'YoY'}),`<div class="tiny-legend"><span><i style="background:#6bbfae"></i>수출금액</span><span><i style="background:#ed766e"></i>YoY</span></div>`)}</div>`+
    `<div class="section"><div class="note">${industrySourceNote} 총수출과 맞춘 공통 기준월은 ${D.industryCommonAsOf}이며, 선택기간 중 산업 데이터가 있는 ${period?`${period.start}—${period.end}`:'기간 없음'}만 계산합니다.</div></div>`+
    `<div class="section grid-2">${card('산업별 수출현황',`${period?`${period.start}—${period.end}`:'비교 가능 기간 없음'} · 산업별 수출`,table)}${card('성장 산업 랭킹','전년 자료가 있는 월의 YoY로 환산',ranking,`<div class="tabs">${[['growth','성장률'],['increase','증가액'],['contribution','기여도']].map(([k,n])=>`<button data-rank="${k}" class="${state.ranking===k?'active':''}">${n}</button>`).join('')}</div>`)}</div>`+
    `<div class="section">${card('규모 × 성장률 Map','산업별 기간 수출규모와 전년 동기간 성장률',map)}</div>`+footer();
  bindCommon();document.querySelectorAll('[data-rank]').forEach(b=>b.onclick=()=>{state.ranking=b.dataset.rank;renderOverview();});document.querySelectorAll('[data-sort]').forEach(b=>b.onclick=()=>{let k=b.dataset.sort;state.desc=state.sort===k?!state.desc:true;state.sort=k;renderOverview();});document.querySelectorAll('[data-industry]').forEach(row=>row.onclick=()=>selectIndustry(row.dataset.industry));
}
function bubbleChart(stats){
  mapStats=stats.filter(r=>r.value>0&&Number.isFinite(r.growth)).sort((a,b)=>b.value-a.value);
  if(!mapStats.length)return '<div class="chart-empty">비교할 수 있는 전년 동기간 데이터가 없습니다.</div>';
  const legend=mapStats.map((r,i)=>`<div class="map-legend-item"><span class="map-legend-number ${r.growth>=0?'up':'down'}">${i+1}</span><strong>${esc(r.name)}</strong><span>${money(r.value)}</span><em class="${delta(r.growth)}">${pct(r.growth)}</em></div>`).join('');
  return `<div class="chart-wrap tall bubble-chart">${bubbleMap(mapStats)}</div><div class="map-legend">${legend}</div><div class="map-caption">가로축: 선택기간 수출액(억 달러, 로그 눈금) · 세로축: 전년 동기간 성장률(%, 극단값을 압축한 눈금) · 원 크기: 절대 증가액. 번호와 아래 목록이 같은 산업입니다.</div>`;
}
function bubbleMap(stats,width=800){
  if(!stats.length)return '<div class="chart-empty">표시할 데이터가 없습니다.</div>';
  const W=Math.max(280,Math.round(width||800)),H=360,L=W<420?55:68,R=22,T=24,B=52,iw=W-L-R,ih=H-T-B;
  const sizes=stats.map(r=>r.value/1e8),logs=sizes.map(Math.log10),logLow=Math.min(...logs),logHigh=Math.max(...logs),logSpan=Math.max(.25,logHigh-logLow);
  const xLow=logLow-logSpan*.1,xHigh=logHigh+logSpan*.1,x=v=>L+(Math.log10(v/1e8)-xLow)/(xHigh-xLow)*iw;
  const compress=v=>Math.asinh(v/.12),transformed=stats.map(r=>compress(r.growth)),rawLow=Math.min(0,...transformed),rawHigh=Math.max(0,...transformed),ySpan=Math.max(.6,rawHigh-rawLow);
  const yLow=rawLow-ySpan*.1,yHigh=rawHigh+ySpan*.1,y=v=>T+(yHigh-compress(v))/(yHigh-yLow)*ih;
  const xCandidates=[];for(let exponent=Math.floor(xLow)-1;exponent<=Math.ceil(xHigh)+1;exponent++)for(const multiplier of [1,2,5]){const value=multiplier*10**exponent;if(Math.log10(value)>=xLow&&Math.log10(value)<=xHigh)xCandidates.push(value);}
  const xTickLimit=Math.max(2,Math.floor(iw/65)),xEvery=Math.ceil(xCandidates.length/xTickLimit);
  const xTicks=xCandidates.filter((_,i)=>i%xEvery===0||i===xCandidates.length-1).map(value=>{const px=x(value*1e8);return `<line x1="${px}" y1="${T}" x2="${px}" y2="${H-B}" stroke="#edf1f5"/><text x="${px}" y="${H-B+17}" text-anchor="middle" font-size="10" fill="#71869a">${num(value)}</text>`;}).join('');
  const candidates=[-10,-5,-2,-1,-.5,-.25,-.1,-.05,0,.05,.1,.2,.3,.5,.75,1,1.5,2,3,5,10];
  const yTicks=[];for(const value of candidates){if(compress(value)<yLow||compress(value)>yHigh)continue;const previous=yTicks.at(-1);if(previous!=null&&Math.abs(y(previous)-y(value))<27){if(value===0)yTicks.pop();else continue;}yTicks.push(value);}
  const yGrid=yTicks.map(value=>`<line x1="${L}" y1="${y(value)}" x2="${W-R}" y2="${y(value)}" stroke="${value===0?'#b6c9d5':'#edf1f5'}" ${value===0?'stroke-dasharray="4 4"':''}/><text x="${L-8}" y="${y(value)+3}" text-anchor="end" font-size="10" fill="#71869a">${num(value*100)}%</text>`).join('');
  const maxIncrease=Math.max(...stats.map(r=>Math.abs(r.increase)),1),points=stats.map((r,i)=>({r,i,radius:8+16*Math.sqrt(Math.abs(r.increase)/maxIncrease)})).sort((a,b)=>b.radius-a.radius);
  const bubbles=points.map(({r,i,radius})=>`<circle cx="${x(r.value)}" cy="${y(r.growth)}" r="${radius}" fill="${r.growth>=0?'#35aa92':'#df7773'}" fill-opacity=".78" stroke="#fff" stroke-width="2" data-tip="${esc(r.name)}<br>수출 ${money(r.value)}<br>성장률 ${pct(r.growth)}<br>증가액 ${money(r.increase)}"/><text x="${x(r.value)}" y="${y(r.growth)+3.5}" text-anchor="middle" font-size="10" font-weight="800" fill="#fff" pointer-events="none">${i+1}</text>`).join('');
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="산업별 기간 수출액과 전년 동기간 성장률 분포"><rect x="${L}" y="${T}" width="${iw}" height="${ih}" fill="#fbfdfe" stroke="#e6edf2"/>${xTicks}${yGrid}${bubbles}<text x="${L}" y="13" font-size="11" font-weight="700" fill="#6d8295">성장률 (%)</text><text x="${L+iw/2}" y="${H-5}" text-anchor="middle" font-size="11" font-weight="700" fill="#6d8295">기간 수출액 (억 달러)</text></svg>`;
}
function selectIndustry(name){state.industry=name;nav('industry');}
function renderIndustryDetail(){
 renderIndustry();decorateIndustry();
 app.querySelector('.page-head .head-actions')?.remove();
 app.querySelector('.mini-list')?.remove();
 const chips=`<div class="card card-pad industry-selector"><div class="chips" role="group" aria-label="산업 선택">${D.industryNames.map(name=>`<button data-industry-chip="${esc(name)}" class="${name===state.industry?'active':''}" aria-pressed="${name===state.industry}">${esc(name)}</button>`).join('')}</div></div>`;
 app.querySelector('.kpi-grid').insertAdjacentHTML('beforebegin',chips);
 app.querySelectorAll('[data-industry-chip]').forEach(button=>button.onclick=()=>selectIndustry(button.dataset.industryChip));
}
function renderIndustry(){
 const stats=industryStats(),selected=stats.find(x=>x.name===state.industry)||stats[0];if(!selected){app.innerHTML=head('INDUSTRY INTELLIGENCE','산업별 수출 분석','선택기간에 산업 데이터가 없습니다.')+footer();return;}
 state.industry=selected.name;const period=selected.period,chartRows=selected.series.filter(r=>r[0]>=period.start&&r[0]<=period.end).map(r=>[r[0],r[2],r[3]]),groups=D.industryCatalogGroups[selected.name]||[];
 const mapped=catalog.filter(x=>groups.includes(x.i)&&D.products[key(x)]?.length).sort((a,b)=>(D.products[key(b)]?.at(-1)?.[1]||0)-(D.products[key(a)]?.at(-1)?.[1]||0)).slice(0,8);
 const itemTable=`<div class="table-scroll"><table class="data-table"><thead><tr><th>대표 품목</th><th>HS</th><th>최근월 수출</th><th>YoY</th></tr></thead><tbody>${mapped.map(item=>{const r=productSeries(item).at(-1);return `<tr data-item="${key(item)}"><td class="name">${esc(item.n)}</td><td>${item.c}</td><td>${money(r?.v)}</td><td class="${delta(r?.yoy)}">${pct(r?.yoy)}</td></tr>`;}).join('')}</tbody></table>${mapped.length?'':'<div class="empty-state">현재 품목 매핑에서 연결된 대표 품목이 없습니다.</div>'}</div>`;
 app.innerHTML=head('INDUSTRY INTELLIGENCE',`${esc(selected.name)} 산업 상세`,'산업별 수출액과 HS 기반 참고 구성을 구분해 보여줍니다.')+`<div class="kpi-grid">${kpi('선택기간 수출액',money(selected.value),`${period.start}—${period.end}`)}${kpi('최근월 YoY',pct(selected.yoy),D.industryCommonAsOf,selected.yoy)}${kpi('최근월 MoM',pct(selected.mom),'전월 대비',selected.mom)}${kpi('전체 수출 비중',pct(selected.share),'동일 기간 총수출 기준')}</div>`+`<div class="section">${card(`${esc(selected.name)} 수출 추이`,'2025-04 이전 HS10 계산값 · 2025-05 이후 공식 발표값',zoomChart('industry',chartRows,{barIndex:1,lineIndex:2,lineUnit:'YoY'}),`<div class="tiny-legend"><span><i style="background:#6bbfae"></i>수출금액</span><span><i style="background:#ed766e"></i>YoY</span></div>`)}</div>`+`<div class="section grid-2">${card('대표 품목',`수동 품목 매핑 · ${mapped.length}개 표시`,itemTable)}${card('데이터 기준','산업 수출액과 HS 참고값을 분리',`<p class="note">${industrySourceNote} 아래 HS 구성과 대표 품목은 탐색용 참고 자료이며 산업 수출액에 맞추기 위해 차액을 배분하지 않습니다.</p>`)}</div>`+footer();
 document.querySelectorAll('[data-item]').forEach(x=>x.onclick=()=>selectItem(x.dataset.item));attachTips();
}
function renderItems(){const industries=['전체',...new Set(catalog.map(x=>x.i))], q=state.query.toLocaleLowerCase('ko');const shown=catalog.filter(x=>(state.catalogIndustry==='전체'||x.i===state.catalogIndustry)&&(!q||[x.c,x.n,x.o,x.p].some(v=>String(v||'').toLocaleLowerCase('ko').includes(q))));app.innerHTML=head('ITEM EXPLORER','품목 탐색','산업에서 품목으로. HS 4·6·10단위를 같은 화면에서 찾습니다.')+`<div class="card card-pad"><div class="chips">${industries.map(x=>`<button data-chip="${esc(x)}" class="${state.catalogIndustry===x?'active':''}">${esc(x)}</button>`).join('')}</div><div class="searchline"><input id="item-search" class="control" type="search" placeholder="품목명, 기업명 또는 HS CODE 검색" value="${esc(state.query)}"><span class="count">${shown.length}개 품목</span></div><div class="tier-grid">${[['HS4','산업 전체'],['HS6','품목군 · 지역 조회 단위'],['HS10','세부 품목']].map(([level,note])=>`<div class="card card-pad tier-card"><h3>${level}</h3><div class="tier-note">${note}</div>${shown.filter(x=>x.l===level).map(x=>`<button class="item-row" data-item="${key(x)}"><em>→</em><b>${esc(x.n)}</b><small>${esc(x.c)} · ${esc(x.i)}${x.m?' · 수입 연계':''}</small></button>`).join('')||'<div class="empty-state">검색 결과 없음</div>'}</div>`).join('')}</div></div>`+footer();document.querySelectorAll('[data-chip]').forEach(x=>x.onclick=()=>{state.catalogIndustry=x.dataset.chip;renderItems();});const input=document.querySelector('#item-search');input.oninput=()=>{state.query=input.value;const cursor=input.selectionStart;renderItems();const next=document.querySelector('#item-search');next.focus();next.setSelectionRange(cursor,cursor);};document.querySelectorAll('[data-item]').forEach(x=>x.onclick=()=>selectItem(x.dataset.item));}
function productSeries(item){return (D.products[key(item)]||[]).map(r=>({date:r[0],v:r[1],w:r[2],imp:r[3],unit:r[4],mom:r[5],yoy:r[6],unitYoy:r[7]}));}
function quarterlySeries(item){return (D.productQuarterly[key(item)]||[]).map(r=>({date:r[0],v:r[1],w:r[2],imp:r[3],unit:r[4],mom:r[5],yoy:r[6],unitYoy:r[7]}));}
function selectItem(id){state.selected=id;state.country='ALL';state.page='detail';render();}
function renderDetail(){const item=catalog.find(x=>key(x)===state.selected);if(!item){nav('items');return;}const monthly=productSeries(item), series=state.productPeriod==='quarter'?quarterlySeries(item):monthly, latest=series.at(-1), base=monthly.at(-1), chartRows=series.slice(state.productPeriod==='quarter'?-24:-36).map(r=>[r.date,r.v,r.unit]),yoyRows=series.slice(state.productPeriod==='quarter'?-24:-36).map(r=>[r.date,r.yoy]);const allCountry=D.countryItems[key(item)]||[],currentMonth=allCountry.map(r=>r[0]).sort().at(-1),countryLatest=new Map(),countryPrev=new Map();allCountry.filter(r=>r[0]===currentMonth).forEach(r=>countryLatest.set(r[1],(countryLatest.get(r[1])||0)+r[2]));allCountry.filter(r=>r[0]===monthBack(currentMonth||D.asOf,12)).forEach(r=>countryPrev.set(r[1],(countryPrev.get(r[1])||0)+r[2]));const total=[...countryLatest.values()].reduce((a,b)=>a+b,0),rank=[...countryLatest].sort((a,b)=>b[1]-a[1]).slice(0,10);const countryOpts=['ALL',...rank.map(r=>r[0])];if(!countryOpts.includes(state.country))state.country='ALL';const countryTrend=allCountry.filter(r=>state.country==='ALL'||r[1]===state.country).reduce((m,r)=>{m.set(r[0],(m.get(r[0])||0)+r[2]);return m;},new Map());const countryRows=[...countryTrend].sort((a,b)=>a[0].localeCompare(b[0])).map(([date,v])=>[date,v]);const countryMap=new Map(countryRows);const countryChart=countryRows.map(r=>[r[0],r[1],countryMap.get(monthBack(r[0],12))?r[1]/countryMap.get(monthBack(r[0],12))-1:null]);const price=latest?.unit==null?'—':`${num(latest.unit)} 달러/kg`;const itemName=esc(item.n);
 app.innerHTML=`<button class="detail-back" id="detail-back">← 품목 탐색으로</button><div class="detail-header">${head(esc(item.i).toUpperCase(),itemName,`${esc(item.o||item.n)} · HS ${item.c}`)}<span class="hs-tag">${item.l}</span></div>${D.products[key(item)]?'':'<div class="note">이 HS코드의 시계열이 현재 대시보드에 없습니다. 원본 데이터와 매핑을 확인한 뒤 데이터를 다시 생성하세요.</div>'}`+
 `<div class="kpi-grid">${kpi('최근 수출금액',money(latest?.v),`${latest?.date||'—'} · ${state.productPeriod==='quarter'?'분기':'월간'}`)}${kpi('수출금액 YoY',pct(latest?.yoy),'전년 동기 대비',latest?.yoy)}${kpi(state.productPeriod==='quarter'?'QoQ':'MoM',pct(latest?.mom),'직전 기간 대비',latest?.mom)}${kpi('평균 수출단가',price,`전년 동기 대비 ${pct(latest?.unitYoy)}`,latest?.unitYoy)}</div>`+
 `<div class="section">${card('수출금액 + 수출단가','수출단가 = 수출금액 ÷ 수출중량 · 중량이 0인 기간은 제외',zoomChart('product-main',chartRows,{barIndex:1,lineIndex:2,linePercent:false,lineUnit:'달러/kg'}),`<div class="tabs"><button data-period="month" class="${state.productPeriod==='month'?'active':''}">월간</button><button data-period="quarter" class="${state.productPeriod==='quarter'?'active':''}">분기</button></div>`)}</div>`+
 `<div class="section grid-2 equal">${card('수출금액 YoY','메인 차트와 분리한 성장률 추이',zoomChart('product-yoy',yoyRows,{height:190},'spark'))}${card('관련 국내 기업','현재 수동 매핑 v1 · 자동 추정 아님',`<div class="company-list">${String(item.p||'').split(',').map(s=>s.trim()).filter(Boolean).map(s=>`<span>${esc(s)}</span>`).join('')||'<div class="empty-state">매핑된 기업이 없습니다.</div>'}</div><p class="hint">기업 이름은 해당 품목의 수출 실적을 기업별로 분해한 값이 아닙니다.</p>`)}</div>`+
 `<div class="section country-grid">${card('국가별 수출 Top 10',`${currentMonth||'—'} 기준 · 금액 / 비중 / YoY`,`<div>${rank.map(([code,v])=>`<div class="country-bar"><b>${esc(D.countryNames[code]||code)}</b><span class="track"><span class="fill" style="display:block;width:${rank[0]?.[1]?v/rank[0][1]*100:0}%"></span></span><strong>${money(v)}</strong></div>`).join('')||'<div class="empty-state">국가별 데이터가 없습니다.</div>'}</div><div class="hint">Top 10 외 국가도 전체 합계에 포함됩니다.</div>`)}${card('국가별 수출 추이','선택 국가의 월별 수출액과 YoY',zoomChart('product-country',countryChart,{barIndex:1,lineIndex:2,lineUnit:'YoY'}),`<select class="control" id="country-select">${countryOpts.map(c=>`<option value="${c}" ${c===state.country?'selected':''}>${c==='ALL'?'전체':esc(D.countryNames[c]||c)}</option>`).join('')}</select>`)}</div>`+
 `<div class="section">${card('원데이터 표',`최근 24개 ${state.productPeriod==='quarter'?'분기':'월'} · 금액은 USD, 중량은 kg`,`<div class="table-scroll"><table class="data-table"><thead><tr><th>기간</th><th>수출금액</th><th>YoY</th><th>${state.productPeriod==='quarter'?'QoQ':'MoM'}</th><th>수출단가 ($/kg)</th><th>단가 YoY</th></tr></thead><tbody>${[...series].reverse().slice(0,24).map(r=>`<tr><td class="name">${r.date}</td><td>${money(r.v)}</td><td class="${delta(r.yoy)}">${pct(r.yoy)}</td><td class="${delta(r.mom)}">${pct(r.mom)}</td><td>${r.unit==null?'—':num(r.unit)}</td><td class="${delta(r.unitYoy)}">${pct(r.unitYoy)}</td></tr>`).join('')}</tbody></table></div>`)}</div>`+footer();
 document.querySelector('#detail-back').onclick=()=>nav('items');document.querySelectorAll('[data-period]').forEach(x=>x.onclick=()=>{state.productPeriod=x.dataset.period;renderDetail();});document.querySelector('#country-select').onchange=e=>{state.country=e.target.value;renderDetail();};attachTips();}
function footer(){return `<div class="footer">자료: 총수출·품목·국가별은 관세청 수출입 API(기준월 ${D.asOf}). 산업별 수출은 ${D.industryCoverageStart}—2025-04 관세청 HS10 계산값, 2025-05—${D.industryAsOf} 산업통상자원부 발표값(공통 기준월 ${D.industryCommonAsOf}). 잠정수출 제외. HS 참고 구성과 상품·기업 연결은 수정 가능한 수동 매핑입니다.</div>`;}
function bindCommon(){attachTips();scheduleChartLayout();}
function decorateIndustry(){
 const name=state.industry,latest=D.industryCommonAsOf,prior=monthBack(latest,1),year=monthBack(latest,12),groups=D.industryHsGroups[name]||[];
 const rows=D.subindustries.filter(r=>groups.includes(r[1])),codes=[...new Set(rows.map(r=>r[2]))];
 const total=rows.filter(r=>r[0]===latest).reduce((a,r)=>a+r[3],0);
 const mapped=new Map(catalog.filter(x=>x.l==='HS4').map(x=>[x.c,x.n]));
 const sub=codes.map(code=>{const value=rows.find(r=>r[0]===latest&&r[2]===code)?.[3]||0,base=rows.find(r=>r[0]===year&&r[2]===code)?.[3]||0,prev=rows.find(r=>r[0]===prior&&r[2]===code)?.[3]||0;return{code,name:mapped.get(code)||`HS ${code}`,value,share:total?value/total:null,yoy:base?value/base-1:null,mom:prev?value/prev-1:null};}).filter(r=>r.value>0).sort((a,b)=>b.value-a.value).slice(0,12);
 const table=`<div class="table-scroll"><table class="data-table"><thead><tr><th>HS 기반 참고 구성</th><th>최근월 수출</th><th>참고 구성 내 비중</th><th>YoY</th><th>MoM</th></tr></thead><tbody>${sub.map(r=>`<tr><td class="name">${esc(r.name)} <span class="industry-pill">${r.code}</span></td><td>${money(r.value)}</td><td>${r.share==null?'—':(r.share*100).toFixed(1)+'%'}</td><td class="${delta(r.yoy)}">${pct(r.yoy)}</td><td class="${delta(r.mom)}">${pct(r.mom)}</td></tr>`).join('')}</tbody></table></div><p class="hint">관세청 HS4 집계를 탐색 편의상 연결한 참고값입니다. 공식 MTI 산업 수출액과 합계·범위가 다르며 차액을 임의 배분하지 않습니다.</p>`;
 app.querySelector('.footer').insertAdjacentHTML('beforebegin',`<div class="section">${card('HS 기반 참고 구성',`${latest} · 연결 그룹 ${groups.map(esc).join(', ')||'없음'} · 상위 12개 HS4`,table)}</div>`);
 const shareKpi=[...app.querySelectorAll('.kpi')].find(x=>x.textContent.includes('전체 수출 비중'));if(shareKpi)shareKpi.querySelector('strong').textContent=selectedShare();
 document.querySelectorAll('[data-industry]').forEach(x=>x.onclick=()=>{state.industry=x.dataset.industry;render();});
 attachTips(app);
}
function selectedShare(){const r=industryStats().find(x=>x.name===state.industry);return r?.share==null?'—':`${(r.share*100).toFixed(1)}%`;}
function decorateDetail(){
 const item=catalog.find(x=>key(x)===state.selected),all=D.countryItems[key(item)]||[],month=all.map(r=>r[0]).sort().at(-1),prev=monthBack(month||D.asOf,12),values=new Map(),bases=new Map();
 const full=productSeries(item),productRows=state.productPeriod==='quarter'?quarterlySeries(item):full;
 const selectedProduct=productRows.filter(r=>isInPeriod(r.date));
 updateZoomChart('product-main',selectedProduct.map(r=>[r.date,r.v,r.unit]),{barIndex:1,lineIndex:2,linePercent:false,lineUnit:'달러/kg'});
 updateZoomChart('product-yoy',selectedProduct.map(r=>[r.date,r.yoy]),{height:190},'spark');
 const rawBody=app.querySelector('.data-table tbody');if(rawBody)rawBody.innerHTML=[...selectedProduct].reverse().map(r=>`<tr><td class="name">${r.date}</td><td>${money(r.v)}</td><td class="${delta(r.yoy)}">${pct(r.yoy)}</td><td class="${delta(r.mom)}">${pct(r.mom)}</td><td>${r.unit==null?'—':num(r.unit)}</td><td class="${delta(r.unitYoy)}">${pct(r.unitYoy)}</td></tr>`).join('')||'<tr><td colspan="6">선택 기간의 품목 데이터가 없습니다.</td></tr>';
 const rawCard=[...app.querySelectorAll('.section .card')].find(x=>x.querySelector('.section-title')?.textContent==='원데이터 표');
 if(rawCard){rawCard.querySelector('.section-sub').textContent=`선택기간 ${state.start}—${state.end} · 금액 USD · 중량 kg`;rawCard.querySelector('.section-head').insertAdjacentHTML('beforeend','<button class="control" id="download-raw-csv">CSV 다운로드 ↓</button>');document.querySelector('#download-raw-csv').onclick=()=>{const header=['period','export_value_usd','export_weight_kg','export_unit_price_usd_per_kg','export_value_yoy_pct','export_value_mom_pct','unit_price_yoy_pct'],lines=selectedProduct.map(r=>[r.date,r.v,r.w,r.unit??'',r.yoy??'',r.mom??'',r.unitYoy??''].join(',')),blob=new Blob(['\ufeff',header.join(','),'\n',lines.join('\n')],{type:'text/csv;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`export_${item.c}_${state.start}_${state.end}.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};}
 {const rows=all.filter(r=>state.country==='ALL'||r[1]===state.country),totals=new Map();rows.forEach(r=>totals.set(r[0],(totals.get(r[0])||0)+r[2]));const countryChart=[...totals].filter(([d])=>d>=state.start&&d<=state.end).sort((a,b)=>a[0].localeCompare(b[0])).map(([d,v])=>[d,v,totals.get(monthBack(d,12))?v/totals.get(monthBack(d,12))-1:null]);updateZoomChart('product-country',countryChart,{barIndex:1,lineIndex:2,lineUnit:'YoY'});}
 const countrySubtitle=app.querySelector('.country-grid .card:nth-child(2) .section-sub');if(countrySubtitle)countrySubtitle.textContent=`선택기간 ${state.start}—${state.end} · 국가별 데이터는 2023년 이후`;
 all.filter(r=>r[0]===month).forEach(r=>values.set(r[1],(values.get(r[1])||0)+r[2]));all.filter(r=>r[0]===prev).forEach(r=>bases.set(r[1],(bases.get(r[1])||0)+r[2]));
 const sum=[...values.values()].reduce((a,b)=>a+b,0),rank=[...values].sort((a,b)=>b[1]-a[1]).slice(0,10),parent=app.querySelector('.country-bar')?.parentElement;
 if(parent)parent.innerHTML=`<div class="country-head"><span>국가</span><span>수출 규모</span><span>금액</span><span>비중</span><span>YoY</span></div>${rank.map(([code,v])=>{const yoy=bases.get(code)?v/bases.get(code)-1:null;return `<div class="country-bar"><b>${esc(D.countryNames[code]||code)}</b><span class="track"><span class="fill" style="display:block;width:${rank[0]?.[1]?v/rank[0][1]*100:0}%"></span></span><strong>${money(v)}</strong><strong>${sum?(v/sum*100).toFixed(1):'—'}%</strong><strong class="${delta(yoy)}">${pct(yoy)}</strong></div>`;}).join('')}`;
 document.querySelectorAll('[data-period]').forEach(x=>x.onclick=()=>{state.productPeriod=x.dataset.period;render();});
 document.querySelector('#country-select').onchange=e=>{state.country=e.target.value;render();};
 attachTips(app);
 const hs6=item.c.slice(0,6),regional=D.regions?.[hs6]||[];
 if(!regional.length)return;
 const latest=regional.map(r=>r[0]).sort().at(-1),items=regional.filter(r=>r[0]===latest).sort((a,b)=>b[3]-a[3]),allTotal=items.reduce((a,r)=>a+r[3],0);
 const body=`<div class="table-scroll"><table class="data-table"><thead><tr><th>매핑 지역</th><th>API 시군구</th><th>수출금액</th><th>조회 지역 내 비중</th></tr></thead><tbody>${items.map(r=>`<tr><td class="name">${esc(r[1])}</td><td>${esc(r[2])}</td><td>${money(r[3])}</td><td>${allTotal?(r[3]/allTotal*100).toFixed(1):'—'}%</td></tr>`).join('')}</tbody></table></div><p class="hint">지역 데이터는 HS6 ${hs6} 단위입니다. 이 화면의 ${item.l} 품목 실적과 직접 합산하거나 동일시하지 마세요. 지정 지역만 조회한 값입니다.</p>`;
 app.querySelector('.footer').insertAdjacentHTML('beforebegin',`<div class="section">${card('관련 지역 수출',`${latest} · HS6 ${hs6} · 사용자 지정 지역`,body)}</div>`);
 attachTips(app);
}
function isInPeriod(date){if(date.includes('-Q')){const [year,quarter]=date.split('-Q').map(Number),first=`${year}-${String((quarter-1)*3+1).padStart(2,'0')}`,last=`${year}-${String(quarter*3).padStart(2,'0')}`;return first>=state.start&&last<=state.end;}return date>=state.start&&date<=state.end;}
function setPreset(preset){state.preset=preset;if(preset!=='CUSTOM'){const months={"1Y":12,"3Y":36,"5Y":60,"10Y":120}[preset];state.start=preset==='ALL'?D.summary[0][0]:monthBack(D.asOf,months-1);state.end=D.asOf;}render();}
function syncPeriodControls(){document.querySelectorAll('[data-preset]').forEach(b=>b.classList.toggle('active',b.dataset.preset===state.preset));const custom=document.querySelector('#custom-range');custom.hidden=state.preset!=='CUSTOM';const start=document.querySelector('#start-month'),end=document.querySelector('#end-month');start.min=D.summary[0][0];start.max=D.asOf;end.min=D.summary[0][0];end.max=D.asOf;start.value=state.start;end.value=state.end;}
function renderPlaceholder(){const names={home:'홈',markets:'증시데이터',briefing:'시황',companies:'기업데이터'},descriptions={home:'수출과 증시·시황 데이터가 연결되면 한 화면에서 핵심 신호를 볼 수 있도록 구성할 예정입니다.',markets:'증시 데이터 모듈은 수출데이터 화면을 완성한 다음 연결합니다.',briefing:'시장 시황과 주요 이벤트를 담을 공간입니다. 현재 데이터는 연결되지 않았습니다.',companies:'기업별 재무·주가·수출 연계 정보를 담을 공간입니다. 현재 데이터는 연결되지 않았습니다.'};const title=names[state.section];app.innerHTML=`<div class="card module-placeholder"><div><div class="icon">↗</div><h1>${title}</h1><p>${descriptions[state.section]}</p><button id="back-to-exports">수출데이터로 돌아가기 →</button></div></div>`;document.querySelector('#back-to-exports').onclick=()=>{state.section='exports';render();};}
function nav(page){state.section='exports';state.page=page;render();window.scrollTo({top:0,behavior:'instant'});}
function render(){chartResizeObserver.disconnect();document.querySelectorAll('.global-nav button').forEach(b=>b.classList.toggle('active',b.dataset.section===state.section));document.querySelector('#export-shell').hidden=state.section!=='exports';if(state.section!=='exports'){renderPlaceholder();return;}syncPeriodControls();document.querySelector('#breadcrumb').textContent=({overview:'종합',industry:'산업별 수출',items:'품목별 수출',detail:'품목별 수출 / 품목 상세'})[state.page];document.querySelectorAll('.main-nav button').forEach(b=>b.classList.toggle('active',b.dataset.page===(state.page==='detail'?'items':state.page)));if(state.page==='overview')renderOverview();else if(state.page==='industry')renderIndustryDetail();else if(state.page==='items')renderItems();else{renderDetail();decorateDetail();}scheduleChartLayout();}
document.querySelectorAll('.main-nav button').forEach(b=>b.onclick=()=>nav(b.dataset.page));
document.querySelectorAll('.global-nav button').forEach(b=>b.onclick=()=>{state.section=b.dataset.section;render();window.scrollTo({top:0,behavior:'instant'});});
document.querySelectorAll('[data-preset]').forEach(b=>b.onclick=()=>setPreset(b.dataset.preset));
for(const id of ['start-month','end-month'])document.querySelector(`#${id}`).onchange=()=>{let a=document.querySelector('#start-month').value,b=document.querySelector('#end-month').value;if(a>b)[a,b]=[b,a];state.start=a;state.end=b;state.preset='CUSTOM';render();};
try {
  const response = await fetch('data/dashboard-data.json');
  if (!response.ok) throw Error('데이터 ' + response.status);
  D = await response.json();
  if (D.schemaVersion !== 4 || !Array.isArray(D.catalog) || !D.summary?.length || !D.products || !D.productQuarterly || !D.itemIndustry || !D.industries?.length || !D.hsIndustries?.length) throw Error('웹 데이터 형식이 맞지 않습니다. 생성·검증 명령을 다시 실행하세요.');
  catalog = D.catalog.filter(item => item.enabled !== false);
  if (!D.industryNames.includes(state.industry)) state.industry = D.industryNames[0];
  if (catalog.some(item => !D.products[key(item)] || !D.productQuarterly[key(item)])) throw Error('품목 매핑과 시계열 연결이 일치하지 않습니다. 웹 데이터를 다시 생성하세요.');
  state.end = D.asOf;
  state.start = monthBack(D.asOf, 11);
  document.querySelector('#asof').textContent = '기준월 ' + D.asOf;
  document.querySelector('#data-status').textContent = D.status === 'final' ? '확정' : '잠정';
  document.querySelector('#last-updated').textContent = D.updatedAt ? '최근 업데이트 ' + new Date(D.updatedAt).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '최근 업데이트 시각 미확인';
  document.querySelector('#source-status').textContent = catalog.length + '개 매핑 품목 · 공식 MTI ' + D.industryNames.length + '개 산업';
  render();
} catch (error) {
  app.innerHTML = '<div class="card card-pad"><h2>대시보드 데이터를 열 수 없습니다</h2><p>' + esc(error.message) + '</p><p class="note">웹 데이터 생성·검증 후 새로고침하세요.</p></div>';
}
