// 캘린더 '실적 모아보기' 탭: 분기보고서 기준 실적을 한 표로 모아 컨센서스 대비와 스탁이지 산업 필터를 제공.
import {reportRows} from './calendar-events.mjs?v=20261126-report';

const state={period:null,sectors:new Set(),sub:'',query:'',beat:'',consOnly:false,sort:'marketCap',desc:true};
const METRICS=[['revenue','매출액'],['operatingProfit','영업이익'],['netIncome','순이익']];
const SORTS={
  company:r=>r.company,sector:r=>r.sector+' '+r.subSector,date:r=>r.date,marketCap:r=>r.marketCap,
  revenue:r=>r.revenue.actual,revenueYoy:r=>r.revenue.yoy,revenueVs:r=>r.revenue.vs?.percent,
  operatingProfit:r=>r.operatingProfit.actual,operatingProfitExp:r=>r.operatingProfit.expected,operatingProfitVs:r=>r.operatingProfit.vs?.percent,operatingProfitYoy:r=>r.operatingProfit.yoy,operatingProfitQoq:r=>r.operatingProfit.qoq,
  netIncome:r=>r.netIncome.actual,netIncomeVs:r=>r.netIncome.vs?.percent,
};
// 1조 이상은 조 단위, 미만은 억 단위 (모두 정수).
const amount=n=>!Number.isFinite(n)?'—':Math.abs(n)>=10000?`${Math.round(n/10000).toLocaleString('ko-KR')}조`:`${Math.round(n).toLocaleString('ko-KR')}억`;
const opSign=r=>{const v=r.operatingProfit.vs;return !v?0:v.percent===null?(v.positive?1:-1):Math.sign(v.percent);};

export function renderSummary(el,quarterly,sectors,{esc}){
  const periods=Object.keys(quarterly?.quarters||{}).sort().reverse();
  if(!periods.includes(state.period))state.period=periods[0]||null;
  const quarter=quarterly?.quarters?.[state.period];
  if(!quarter){el.innerHTML='<div class="es-empty">분기보고서 실적 데이터가 아직 없습니다.</div>';return;}
  const all=reportRows(quarter,sectors);
  const consRows=r=>!state.consOnly||r.operatingProfit.expected!==null||r.revenue.expected!==null;
  const matchBeat=r=>!state.beat||(state.beat==='none'?!r.operatingProfit.vs:state.beat==='beat'?opSign(r)>0:opSign(r)<0);
  const q=state.query.trim().toLowerCase();
  const base=all.filter(r=>consRows(r)&&matchBeat(r)&&(!q||r.company.toLowerCase().includes(q)||r.stock.includes(q)));
  const rows=base.filter(r=>(!state.sectors.size||state.sectors.has(r.sector))&&(!state.sub||r.subSector===state.sub));
  const key=SORTS[state.sort]||SORTS.marketCap;
  rows.sort((a,b)=>{const x=key(a),y=key(b);if(x===y)return 0;if(x===null||x===undefined)return 1;if(y===null||y===undefined)return -1;return (typeof x==='string'?x.localeCompare(y,'ko'):x-y)*(state.desc?-1:1);});

  // Chip counts follow every filter except the sector filter itself, so chips show where results are.
  const tally=new Map();
  for(const r of base){const t=tally.get(r.sector)||{n:0,up:0,down:0};t.n++;const s=opSign(r);if(s>0)t.up++;else if(s<0)t.down++;tally.set(r.sector,t);}
  const chipSectors=[...tally.keys()].sort((a,b)=>tally.get(b).n-tally.get(a).n||a.localeCompare(b,'ko'));
  const subs=[...new Set(all.filter(r=>!state.sectors.size||state.sectors.has(r.sector)).map(r=>r.subSector))].sort((a,b)=>a.localeCompare(b,'ko'));
  if(state.sub&&!subs.includes(state.sub))state.sub='';

  const compared=rows.filter(r=>r.operatingProfit.vs),up=compared.filter(r=>opSign(r)>0).length,down=compared.filter(r=>opSign(r)<0).length;
  const pcts=compared.map(r=>r.operatingProfit.vs.percent).filter(Number.isFinite).sort((a,b)=>a-b);
  const median=pcts.length?(pcts.length%2?pcts[(pcts.length-1)/2]:(pcts[pcts.length/2-1]+pcts[pcts.length/2])/2):null;
  const pct=n=>{const t=Math.abs(n)<0.05?'0.0':n.toFixed(1);return `${n>=0.05?'+':''}${t}%`;};

  const vsCell=v=>{
    if(!v)return '<td class="es-num es-muted">—</td>';
    const strong=v.percent===null||Math.abs(v.percent)>=10;
    return `<td class="es-num es-vs ${v.positive?'up':'down'} ${strong?'strong':''}" ${v.description?`title="${esc(v.description)}"`:''}>${esc(v.text)}</td>`;
  };
  const growthCell=(rate,label)=>label?`<td class="es-num ${label==='흑자전환'?'es-up':'es-down'}">${esc(label)}</td>`:Number.isFinite(rate)?`<td class="es-num ${rate>0?'es-up':rate<0?'es-down':''}">${pct(rate)}</td>`:'<td class="es-num es-muted">—</td>';
  const th=(k,label,cls='es-num')=>`<th class="${cls}"><button type="button" data-es-sort="${k}" aria-sort="${state.sort===k?(state.desc?'descending':'ascending'):'none'}">${label}${state.sort===k?(state.desc?' ▾':' ▴'):''}</button></th>`;

  // 검색창은 한 번만 만들고 다시 그리지 않음: 입력창을 갈아끼우면 한글 조합(IME)이 끊김.
  if(!el.querySelector('[data-es="query"]'))el.innerHTML='<div class="es-bar"><input type="search" data-es="query" placeholder="종목명·코드" aria-label="종목 검색"><span class="es-bar-dyn"></span></div><div class="es-body"></div>';
  el.querySelector('[data-es="query"]').value=state.query;
  el.querySelector('.es-bar-dyn').innerHTML=`
    <select data-es="period" aria-label="분기">${periods.map(p=>`<option ${p===state.period?'selected':''}>${esc(p)}</option>`).join('')}</select>
    <select data-es="sub" aria-label="중분류"><option value="">중분류 전체</option>${subs.map(s=>`<option ${s===state.sub?'selected':''}>${esc(s)}</option>`).join('')}</select>
    <select data-es="beat" aria-label="영업이익 컨센 대비"><option value="">영익 컨센 대비 전체</option><option value="beat" ${state.beat==='beat'?'selected':''}>상회</option><option value="miss" ${state.beat==='miss'?'selected':''}>하회</option><option value="none" ${state.beat==='none'?'selected':''}>컨센 없음</option></select>
    <label class="es-check"><input type="checkbox" data-es="consOnly" ${state.consOnly?'checked':''}> 컨센 있는 종목만</label>`;
  el.querySelector('.es-body').innerHTML=`<div class="es-chips" role="group" aria-label="스탁이지 대분류 필터 (여러 개 선택 가능)">
    <button type="button" class="es-chip" data-es-sector="" aria-pressed="${!state.sectors.size}">전체 <b>${base.length}</b></button>
    ${chipSectors.map(s=>{const t=tally.get(s);return `<button type="button" class="es-chip" data-es-sector="${esc(s)}" aria-pressed="${state.sectors.has(s)}">${esc(s)} <b>${t.n}</b>${t.up||t.down?` <span class="es-up">▲${t.up}</span><span class="es-down">▼${t.down}</span>`:''}</button>`;}).join('')}
  </div>
  <div class="es-kpis"><span>발표 <b>${rows.length}</b>곳</span><span>영익 컨센 비교 <b>${compared.length}</b>곳</span><span class="es-up">상회 <b>${up}</b>${compared.length?` (${Math.round(up/compared.length*100)}%)`:''}</span><span class="es-down">하회 <b>${down}</b></span>${median!==null?`<span>영익 서프라이즈 중앙값 <b class="${median>=0?'es-up':'es-down'}">${pct(median)}</b></span>`:''}</div>
  <div class="es-table-wrap"><table class="es-table"><thead><tr>
    ${th('company','종목','')}${th('sector','산업','')}${th('date','공시일','')}${th('marketCap','시총')}
    ${th('revenue','매출액')}${th('operatingProfit','영업이익')}${th('netIncome','순이익')}${th('revenueYoy','매출 YoY')}${th('operatingProfitYoy','영익 YoY')}
    ${th('operatingProfitExp','컨센 영익')}${th('revenueVs','매출 컨센대비')}${th('operatingProfitVs','영익 컨센대비')}
  </tr></thead><tbody>${rows.length?rows.map(r=>`<tr>
    <td><a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer" class="es-name">${esc(r.company)}</a><span class="es-code">${esc(r.stock)}${r.basis&&r.basis!=='연결'?' · '+esc(r.basis):''}</span></td>
    <td><span class="es-sector">${esc(r.sector)}</span><span class="es-code">${esc(r.subSector)}</span></td>
    <td class="es-muted">${esc(r.date.slice(5).replace('-','/'))}</td>
    <td class="es-num">${amount(r.marketCap)}</td>
    <td class="es-num">${amount(r.revenue.actual)}</td><td class="es-num">${amount(r.operatingProfit.actual)}</td><td class="es-num">${amount(r.netIncome.actual)}</td>
    ${growthCell(r.revenue.yoy,r.revenue.yoyLabel)}${growthCell(r.operatingProfit.yoy,r.operatingProfit.yoyLabel)}
    <td class="es-num es-muted">${amount(r.operatingProfit.expected)}</td>${vsCell(r.revenue.vs)}${vsCell(r.operatingProfit.vs)}
  </tr>`).join(''):`<tr><td colspan="12" class="es-empty">조건에 맞는 종목이 없습니다.</td></tr>`}</tbody></table></div>
  <div class="es-note">분기보고서 기준 3개월 실적 (OpenDART 주요계정) · 연결 우선 · 순이익은 비지배지분 포함 · 컨센서스 Quantiwise${quarter.consensusSavedAt?' ('+esc(quarter.consensusSavedAt.slice(0,10))+' 저장)':''} · 대상 ${quarter.universe}곳 중 제출 ${all.length}곳 · 적자 예상치는 절댓값을 분모로 계산 · ±10% 이상 굵게</div>`;

  const rerender=()=>renderSummary(el,quarterly,sectors,{esc});
  el.onclick=e=>{
    const chip=e.target.closest('[data-es-sector]');
    if(chip){const s=chip.dataset.esSector;if(!s){state.sectors.clear();state.sub='';}else if(state.sectors.has(s))state.sectors.delete(s);else state.sectors.add(s);rerender();return;}
    const sort=e.target.closest('[data-es-sort]')?.dataset.esSort;
    if(sort){state.desc=state.sort===sort?!state.desc:!['company','sector'].includes(sort);state.sort=sort;rerender();}
  };
  el.onchange=e=>{const k=e.target.dataset.es;if(!k||k==='query')return;state[k]=e.target.type==='checkbox'?e.target.checked:e.target.value;rerender();};
  el.oninput=e=>{if(e.target.dataset.es!=='query')return;state.query=e.target.value;rerender();};
}
