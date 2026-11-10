// 내부자 매매·증여·대량보유·기업 페이지 공용 함수
const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);
const fmt = n => n==null||n==='' ? '' : Number(n).toLocaleString('ko-KR');
const eok = n => n==null ? '' : (Math.abs(n)/1e8 >= 100 ? Math.round(n/1e8).toLocaleString('ko-KR') : (n/1e8).toFixed(Math.abs(n)/1e8>=1?1:2));
const esc = s => String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const dartLink = no => `<a href="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${no}" target="_blank" rel="noopener">원문</a>`;
const GROUPS = ['최대주주측','임원','주요주주','기타'];
let META = (window.D||window.B||window.C||{}).meta || {};
let LAST = META.range ? META.range[1] : new Date().toISOString().slice(0,10);
const daysAgo = n => { const t=new Date(LAST); t.setDate(t.getDate()-n); return t.toISOString().slice(0,10); };
const lagOf = r => r.lag ?? (r.date ? Math.round((new Date(r.rcept_dt)-new Date(r.date))/864e5) : 0);

// 종목 정보 붙이기: corps[code] = [이름, 시장, 대분류, 중분류]
function withCorp(r, corps){ const c = corps[r.stock_code] || []; r.corp_name=c[0]||r.stock_code; r.market=c[1]; r.sector_l=c[2]; r.sector_m=c[3]; return r; }
// 매매 묶음 행 펼치기
function loadTrades(D){
  const i = Object.fromEntries(D.tcols.map((c,k)=>[c,k]));
  return D.trades.map(a => withCorp({stock_code:a[i.code], person:a[i.person], relation:a[i.rel], position:a[i.pos], holder_group:a[i.grp],
    date:a[i.date], rcept_dt:a[i.rdt], rcept_no:a[i.rno], direction:a[i.dir], kind:a[i.kind], n:a[i.n], qty_change:a[i.qty],
    amount:a[i.amt], pct_chg:a[i.pct], pct_after:a[i.after], loan:a[i.loan], lag:a[i.lag]}, D.corps));
}

function pctTxt(v, up){ if(v==null) return ''; up = up ?? v>0; const a=Math.abs(v);
  return `<span class="${up?'pos':'neg'}">${up?'+':'-'}${a>=0.005?a.toFixed(2):'<0.01'}</span>`; }
const pctAfter = v => v==null ? '' : Number(v).toFixed(2);
const secCell = r => r.sector_l ? `${esc(r.sector_l)}${r.sector_m?`<div class="rel">${esc(r.sector_m)}</div>`:''}` : '<span class="rel">미분류</span>';
const secKey = r => (r.sector_l||'힣')+(r.sector_m||'');
const corpCell = r => `${r.stock_code?`<a class="corp clink" href="company.html?code=${r.stock_code}" title="기업 페이지">${esc(r.corp_name)}</a>`:`<span class="corp">${esc(r.corp_name)}</span>`}<span class="code">${r.stock_code||''} ${r.market||''}</span>`;
const dateCell = r => { const lag=lagOf(r); return lag>30 ? `${r.date}<div class="rel">늦은 보고 ${fmt(lag)}일</div>` : (r.date||''); };
const loanBadge = amt => amt ? `<span class="badge loan">차입 ${eok(amt)}억</span>` : '';
const whoCell = r => `<div class="who">${esc(r.person||'')}<div class="rel">${esc([r.relation,r.position].filter(Boolean).join(' · '))}</div></div>`;

function nav(active){
  const items = [['trades.html','내부자 매매'],['gifts.html','증여'],['bulk.html','대량보유']];
  return `<nav class="top">${items.map(([h,l])=>`<a href="${h}" class="${h===active?'on':''}">${l}</a>`).join('')}</nav>`;
}
function metaText(){ return `${META.range?META.range[0]+' ~ '+META.range[1]:''} · 종가 ${META.price_last||'-'} 기준 · 생성 ${(META.generated_at||'').replace('T',' ').slice(0,16)}`; }

// 페이지별 상태 저장(브라우저에만)
function loadState(key, def){ try{ return Object.assign(def, JSON.parse(localStorage.getItem(key)||'{}')); }catch(e){ return def; } }
function saveState(key, S, keys){ try{ localStorage.setItem(key, JSON.stringify(Object.fromEntries(keys.map(k=>[k, S[k] instanceof Set ? [...S[k]] : S[k]])))); }catch(e){} }

function segHTML(id, opts, cur){ return `<span class="seg" id="${id}">${opts.map(([v,l])=>`<span class="${String(cur)===String(v)?'on':''}" data-v="${v}">${l}</span>`).join('')}</span>`; }
function bindSeg(id, fn){ $$(`#${id} span`).forEach(e=>e.onclick=()=>fn(e.dataset.v)); }
function marketSelect(cur){ return `<select id="mk"><option value="">전체 시장</option>${['코스피','코스닥'].map(m=>`<option ${cur===m?'selected':''}>${m}</option>`).join('')}</select>`; }
function cardsHTML(c){ return c.map(([k,v,s])=>`<div class="card"><div class="k">${k}</div><div class="v">${typeof v==='number'?fmt(v):v}</div><div class="s">${s}</div></div>`).join(''); }

// 정렬 가능한 표. cols: [제목, 셀함수, 정렬키함수, 숫자열?]
function renderTable(el, cols, rows, st, onRow){
  const si = st.sort ?? st.defSort ?? 0, key = cols[si][2];
  rows = rows.slice().sort((a,b)=>{ const x=key(a), y=key(b);
    const c = (typeof x==='number'&&typeof y==='number') ? x-y : String(x??'').localeCompare(String(y??''));
    return st.desc===false ? c : -c; });
  const shown = rows.slice(0,1500);
  el.innerHTML = rows.length ? `<table><thead><tr>${cols.map((c,i)=>`<th class="${c[3]?'num':''}" data-i="${i}">${c[0]}${si===i?(st.desc===false?' ▴':' ▾'):''}</th>`).join('')}</tr></thead><tbody>`+
    shown.map((r,k)=>`<tr data-k="${k}" class="${onRow?'click':''}">${cols.map(c=>`<td class="${c[3]?'num':''}">${c[1](r)}</td>`).join('')}</tr>`).join('')+'</tbody></table>'+
    (rows.length>1500?`<div class="empty">${fmt(rows.length)}행 중 1,500행만 표시합니다. 기간·검색으로 좁혀주세요.</div>`:'')
    : `<div class="empty">조건에 맞는 공시가 없습니다.</div>`;
  el.querySelectorAll('th[data-i]').forEach(e=>e.onclick=()=>{ const i=+e.dataset.i; if(st.sort===i) st.desc = st.desc===false; else { st.sort=i; st.desc=true; } renderTable(el, cols, rows, st, onRow); });
  if(onRow) el.querySelectorAll('tr[data-k]').forEach(e=>e.onclick=ev=>{ if(ev.target.closest('a')) return; onRow(shown[+e.dataset.k]); });
}
