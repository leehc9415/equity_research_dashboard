/* 매크로 보드 · 지표 브리핑 — 샘플 렌더러 (의존성 없음, file:// 실행 가능)
 * 데이터: window.MACRO_DATA (collectors/macro/build.py 가 생성)
 * 숫자는 모두 빌드 단계에서 계산되고, 여기서는 블록을 그리기만 한다. */
(function () {
  'use strict';
  const D = window.MACRO_DATA;
  const main = document.getElementById('main');
  const tip = document.getElementById('tip');
  const PAL = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948'];
  const NO_SPACE_UNITS = new Set(['%', 'k', 'bp', '%p', 'M', '억$', '조$', '원', '']);
  const mounts = [];

  if (!D) { main.innerHTML = '<div class="card" style="padding:30px">data/macro-data.js 가 없습니다. collectors/macro/build.py 를 실행하세요.</div>'; return; }

  // ───────── 포맷
  function fmt(v, d = 1, sign = false, unit = '') {
    if (v === null || v === undefined || Number.isNaN(v)) return '–';
    const r = Number(v.toFixed(d));
    const a = Math.abs(r).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
    const pre = r < 0 ? '−' : (sign && r > 0 ? '+' : '');
    if (unit === '$') return pre + '$' + a;
    return pre + a + (unit ? (NO_SPACE_UNITS.has(unit) ? '' : ' ') + unit : '');
  }
  const dir = (v, d = 1) => (v == null ? 'flat' : Number(v.toFixed(d)) > 0 ? 'up' : Number(v.toFixed(d)) < 0 ? 'down' : 'flat');
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const ymLabel = (d) => `${d.slice(2, 4)}.${d.slice(5, 7)}`;

  // ───────── 툴팁
  function showTip(html, ev) {
    tip.innerHTML = html; tip.classList.add('show');
    const w = tip.offsetWidth, h = tip.offsetHeight;
    let x = ev.clientX + 14, y = ev.clientY - h - 10;
    if (x + w > innerWidth - 8) x = ev.clientX - w - 14;
    if (y < 8) y = ev.clientY + 16;
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  }
  const hideTip = () => tip.classList.remove('show');

  // ───────── 공통 SVG
  const NS = 'http://www.w3.org/2000/svg';
  function el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function niceTicks(lo, hi, n = 5) {
    if (lo === hi) { lo -= 1; hi += 1; }
    const span = hi - lo, raw = span / n, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) || 10 * mag;
    const t0 = Math.floor(lo / step) * step, t1 = Math.ceil(hi / step) * step, out = [];
    for (let t = t0; t <= t1 + step / 2; t += step) out.push(Number(t.toFixed(10)));
    return out;
  }
  const tickDec = (ticks) => { const s = ticks[1] - ticks[0]; return s >= 1 ? 0 : s >= 0.1 ? 1 : 2; };

  // ───────── 스파크라인
  function drawSpark(box, t) {
    const vals = t.spark || [], n = vals.length;
    if (n < 2) return;
    const W = box.clientWidth || 160, H = 30;
    const ok = vals.filter((v) => v != null);
    let lo = Math.min(...ok), hi = Math.max(...ok);
    if (lo === hi) { lo -= 1; hi += 1; }
    const X = (i) => (i / (n - 1)) * W, Y = (v) => H - 2 - ((v - lo) / (hi - lo)) * (H - 4);
    const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none' }, box);
    let dpath = '', apath = '', pen = false, firstX = null, lastX = null;
    vals.forEach((v, i) => {
      if (v == null) { pen = false; return; }
      dpath += (pen ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(v).toFixed(1);
      if (firstX === null) firstX = X(i);
      lastX = X(i); pen = true;
    });
    apath = dpath + `L${lastX.toFixed(1)} ${H}L${firstX.toFixed(1)} ${H}Z`;
    el('path', { d: apath, fill: '#2a78d6', 'fill-opacity': 0.07 }, svg);
    el('path', { d: dpath, fill: 'none', stroke: '#3a6a9c', 'stroke-width': 1.6, 'stroke-linejoin': 'round' }, svg);
    const li = vals.length - 1 - [...vals].reverse().findIndex((v) => v != null);
    el('circle', { cx: X(li), cy: Y(vals[li]), r: 2.6, fill: '#14364d' }, svg);
    const dot = el('circle', { r: 3, fill: '#fff', stroke: '#14364d', 'stroke-width': 1.5, opacity: 0 }, svg);
    const hit = el('rect', { x: 0, y: -4, width: W, height: H + 8, fill: 'transparent' }, svg);
    hit.addEventListener('mousemove', (ev) => {
      const r = svg.getBoundingClientRect();
      let i = Math.round(((ev.clientX - r.left) / r.width) * (n - 1));
      i = Math.max(0, Math.min(n - 1, i));
      while (i > 0 && vals[i] == null) i--;
      if (vals[i] == null) return;
      dot.setAttribute('cx', X(i)); dot.setAttribute('cy', Y(vals[i])); dot.setAttribute('opacity', 1);
      const dd = t.sparkD ? t.sparkD[i] : '';
      const lab = t.freq === 'M' ? ymLabel(dd) : t.freq === 'Q' ? `${dd.slice(0, 4)}Q${Math.floor((+dd.slice(5, 7) - 1) / 3) + 1}` : dd;
      showTip(`<div class="tt-d">${lab}</div><b>${fmt(vals[i], t.d, false, t.u)}</b>`, ev);
    });
    hit.addEventListener('mouseleave', () => { dot.setAttribute('opacity', 0); hideTip(); });
  }

  // ───────── 시계열 차트 (선/막대/콤보, 단일 축)
  function drawChart(box, bl) {
    box.innerHTML = '';
    const W = Math.max(box.clientWidth, 280), H = box.clientHeight || 236;
    const lineSeries = bl.series.filter((s) => s.kind !== 'bar');
    const m = { l: 40, r: lineSeries.length ? 52 : 12, t: 10, b: 24 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b, n = bl.x.length;
    const hasBar = bl.series.some((s) => s.kind === 'bar');
    const all = bl.series.flatMap((s) => s.values).filter((v) => v != null);
    if (!all.length) { box.innerHTML = '<div class="note">데이터 없음</div>'; return; }
    let lo = Math.min(...all), hi = Math.max(...all);
    if (bl.zero || hasBar || (lo < 0 && hi > 0)) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    const pad = (hi - lo) * 0.06; if (!(bl.zero || hasBar) || lo < 0) lo -= pad; hi += pad;
    const ticks = niceTicks(lo, hi, 5), y0 = ticks[0], y1 = ticks[ticks.length - 1];
    const Y = (v) => m.t + ih - ((v - y0) / (y1 - y0)) * ih;
    const band = iw / n;
    const X = hasBar ? (i) => m.l + (i + 0.5) * band : (i) => m.l + (n === 1 ? iw / 2 : (i / (n - 1)) * iw);
    const svg = el('svg', { viewBox: `0 0 ${W} ${H}` }, box);
    const td = tickDec(ticks);
    ticks.forEach((t) => {
      el('line', { x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t), stroke: t === 0 ? '#c3ced9' : '#eef2f6', 'stroke-width': 1 }, svg);
      const tx = el('text', { x: m.l - 6, y: Y(t) + 3.5, 'text-anchor': 'end', 'font-size': 10, fill: '#8b9bad' }, svg);
      tx.textContent = fmt(t, td);
    });
    // x 라벨
    const daily = bl.x.length > 2 && (new Date(bl.x[1]) - new Date(bl.x[0])) / 864e5 < 20;
    const spanDays = (new Date(bl.x[n - 1]) - new Date(bl.x[0])) / 864e5;
    const nLab = Math.min(7, n), step = Math.max(1, Math.round((n - 1) / (nLab - 1 || 1)));
    for (let i = n - 1; i >= 0; i -= step) {
      const d = bl.x[i];
      const s = daily && spanDays < 200 ? `${d.slice(5, 7)}/${d.slice(8, 10)}` : ymLabel(d);
      const tx = el('text', { x: X(i), y: H - 6, 'text-anchor': 'middle', 'font-size': 10, fill: '#8b9bad' }, svg);
      tx.textContent = s;
    }
    // 막대
    const barS = bl.series.map((s, k) => ({ s, k })).filter((o) => o.s.kind === 'bar');
    const bw = Math.max(1.5, Math.min(18, band * 0.66 / Math.max(1, barS.length)));
    barS.forEach(({ s, k }, bi) => {
      s.values.forEach((v, i) => {
        if (v == null) return;
        const x = X(i) - (bw * barS.length) / 2 + bi * bw + 0.5, w = bw - 1;
        const ya = Y(0), yb = Y(v), top = Math.min(ya, yb), h = Math.max(1, Math.abs(yb - ya));
        const r = Math.min(3, w / 2, h);
        const dpath = v >= 0
          ? `M${x} ${ya}V${top + r}Q${x} ${top} ${x + r} ${top}H${x + w - r}Q${x + w} ${top} ${x + w} ${top + r}V${ya}Z`
          : `M${x} ${ya}V${top + h - r}Q${x} ${top + h} ${x + r} ${top + h}H${x + w - r}Q${x + w} ${top + h} ${x + w} ${top + h - r}V${ya}Z`;
        el('path', { d: dpath, fill: PAL[k], 'fill-opacity': 0.85 }, svg);
      });
    });
    // 선
    const ends = [];
    bl.series.forEach((s, k) => {
      if (s.kind === 'bar') return;
      let d = '', pen = false, last = null;
      s.values.forEach((v, i) => {
        if (v == null) { pen = false; return; }
        d += (pen ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(v).toFixed(1); pen = true; last = i;
      });
      // 고립된 점(앞뒤 결측)은 마커로
      s.values.forEach((v, i) => {
        if (v != null && s.values[i - 1] == null && s.values[i + 1] == null) el('circle', { cx: X(i), cy: Y(v), r: 2.5, fill: PAL[k] }, svg);
      });
      el('path', { d, fill: 'none', stroke: PAL[k], 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }, svg);
      if (last != null) ends.push({ k, y: Y(s.values[last]), x: X(last), v: s.values[last] });
    });
    // 끝값 라벨 (겹침 회피)
    ends.sort((a, b) => a.y - b.y);
    for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 12) ends[i].y = ends[i - 1].y + 12;
    ends.forEach((e) => {
      el('circle', { cx: e.x, cy: Y(bl.series[e.k].values.filter((v) => v != null).slice(-1)[0]), r: 3, fill: PAL[e.k], stroke: '#fff', 'stroke-width': 1.5 }, svg);
      el('rect', { x: W - m.r + 6, y: e.y - 4, width: 7, height: 7, rx: 2, fill: PAL[e.k] }, svg);
      const tx = el('text', { x: W - m.r + 16, y: e.y + 3, 'font-size': 10, fill: '#36516a', 'font-weight': 700 }, svg);
      tx.textContent = fmt(e.v, bl.d);
    });
    // 호버 레이어
    const cross = el('line', { y1: m.t, y2: m.t + ih, stroke: '#9fb0c2', 'stroke-dasharray': '3 3', opacity: 0 }, svg);
    const hit = el('rect', { x: m.l, y: m.t, width: iw, height: ih, fill: 'transparent' }, svg);
    hit.addEventListener('mousemove', (ev) => {
      const r = svg.getBoundingClientRect(), px = ((ev.clientX - r.left) / r.width) * W;
      let i = hasBar ? Math.floor((px - m.l) / band) : Math.round(((px - m.l) / iw) * (n - 1));
      i = Math.max(0, Math.min(n - 1, i));
      cross.setAttribute('x1', X(i)); cross.setAttribute('x2', X(i)); cross.setAttribute('opacity', 1);
      const d = bl.x[i];
      const rows = bl.series.map((s, k) => `<div><i style="background:${PAL[k]}"></i>${esc(s.name)} <b>${fmt(s.values[i], bl.d, false, bl.unit === 'k' ? 'k' : bl.unit === '%' ? '%' : '')}</b></div>`).join('');
      showTip(`<div class="tt-d">${daily ? d : `${d.slice(0, 4)}년 ${+d.slice(5, 7)}월`}</div>${rows}`, ev);
    });
    hit.addEventListener('mouseleave', () => { cross.setAttribute('opacity', 0); hideTip(); });
  }

  // ───────── 블록 렌더러
  function legend(series) {
    if (series.length < 2) return '';
    return `<span class="legend">${series.map((s, k) => `<span><i class="${s.kind === 'bar' ? '' : 'ln'}" style="background:${PAL[k]}"></i>${esc(s.name)}</span>`).join('')}</span>`;
  }

  function blockCards(bl) {
    const items = bl.items.map((c) => {
      const missing = c.value == null && !c.valueText;
      const val = c.valueText ? esc(c.valueText) : missing ? esc(c.missing || '–') : fmt(c.value, c.d, c.s, c.u);
      const su = c.u === '%' ? '%p' : c.u;
      const rows = [];
      if (c.cons != null) rows.push(`<span><i>예상</i>${fmt(c.cons, c.d, c.s, c.u)}</span>`);
      if (c.prior != null) rows.push(`<span><i>${c.priorNote ? '이전(' + esc(c.priorNote) + ')' : '이전'}</i>${fmt(c.prior, c.d, c.s, c.u)}</span>`);
      const surp = c.surprise != null ? `<span class="surp ${dir(c.surprise, c.d)}">서프라이즈 ${fmt(c.surprise, c.d, true, su)}</span>` : '';
      return `<div class="kc"><div class="lb">${esc(c.label)}</div><div class="vl ${missing ? 'missing' : ''}">${val}</div>
        ${rows.length ? `<div class="rw">${rows.join('')}</div>` : ''}${surp}${c.foot ? `<div class="ft">${esc(c.foot)}</div>` : ''}</div>`;
    }).join('');
    return `<div class="blk card full"><div class="cards">${items}</div>${bl.consSource ? `<div class="note">컨센서스 출처: ${esc(bl.consSource)}</div>` : ''}</div>`;
  }

  function blockBars(bl) {
    const items = bl.items.slice();
    const vals = items.map((x) => x.value).concat(bl.total ? [bl.total.value] : []);
    const hasNeg = vals.some((v) => v < 0);
    const max = Math.max(...vals.map(Math.abs), 1e-9);
    const zeroPct = hasNeg ? (Math.abs(Math.min(...vals, 0)) / (Math.abs(Math.min(...vals, 0)) + Math.max(...vals, 0))) * 100 : 0;
    const scale = hasNeg ? 100 / (Math.abs(Math.min(...vals, 0)) + Math.max(...vals, 0)) : 100 / max;
    const row = (x, cls = '') => {
      const w = Math.abs(x.value) * scale, pos = x.value >= 0;
      const left = pos ? zeroPct : zeroPct - w;
      return `<div class="hb ${cls}"><span class="lab" title="${esc(x.label)}">${esc(x.label)}</span><span class="trk">
        <span class="zero" style="left:${zeroPct}%"></span><span class="bar ${pos ? 'pos' : 'neg'}" style="left:${left}%;width:${Math.max(w, 0.4)}%"></span></span>
        <b class="${cls ? '' : dir(x.value, bl.d)}">${fmt(x.value, bl.d, true, bl.unit === '%p' ? '' : bl.unit)}</b></div>`;
    };
    const tot = bl.total ? row(bl.total, 'total') + (bl.total.actual != null ? `<div class="note" style="margin:2px 0 0">실제 헤드라인 ${fmt(bl.total.actual, 2, true, '%')}</div>` : '') : '';
    return `<div class="blk card"><h3>${esc(bl.title)}</h3><div class="hbars">${items.map((x) => row(x)).join('')}${tot}</div>${bl.note ? `<div class="note">${esc(bl.note)}</div>` : ''}</div>`;
  }

  function blockTable(bl) {
    const cols = bl.columns;
    const head = `<tr><th></th>${cols.map((c) => `<th class="${c.hl ? 'hl' : ''}">${esc(c.label)}</th>`).join('')}</tr>`;
    const body = bl.rows.map((r) => `<tr class="${r.emph ? 'emph' : ''}"><td style="padding-left:${8 + (r.depth || 0) * 13}px">${esc(r.label)}</td>${r.values.map((v, i) => {
      const c = cols[i] || {};
      const d = (r.fmt && r.fmt[i] != null) ? r.fmt[i] : (c.d != null ? c.d : 1);
      const unit = i === r.values.length - 1 && r.unitLast ? r.unitLast : '';
      const txt = fmt(v, d, !!c.s, unit);
      return `<td class="${c.hl ? 'hl' : ''}">${c.heat && v != null ? `<span class="heat ${dir(v, d)}">${txt}</span>` : txt}</td>`;
    }).join('')}</tr>`).join('');
    const wide = bl.wide != null ? bl.wide : cols.length > 4;
    return `<div class="blk card ${wide ? 'full' : ''}"><h3>${esc(bl.title)}</h3><div class="tbl-wrap"><table class="dt"><thead>${head}</thead><tbody>${body}</tbody></table></div>${bl.note ? `<div class="note">${esc(bl.note)}</div>` : ''}</div>`;
  }

  function blockChart(bl) {
    const id = mounts.push({ type: 'chart', bl }) - 1;
    return `<div class="blk card"><h3>${esc(bl.title)}${legend(bl.series)}</h3><div class="chart" data-mount="${id}"></div>${bl.note ? `<div class="note">${esc(bl.note)}</div>` : ''}</div>`;
  }

  function blockDiff(bl) {
    const html = bl.segments.map(([op, t]) => {
      const s = esc(t).replace(/\s*¶\s*/g, '<span class="pb"></span>');
      return op === '=' ? s : op === '-' ? `<del>${s}</del>` : `<ins>${s}</ins>`;
    }).join(' ');
    return `<div class="blk card full"><h3>${esc(bl.title)}</h3><div class="diff">${html}</div>${bl.note ? `<div class="note">${esc(bl.note)}</div>` : ''}</div>`;
  }

  function blockDist(bl) {
    const groups = bl.groups.map((g) => {
      const mx = Math.max(...g.items.map((x) => x.p));
      return `<div class="dist-g"><div class="gl">${esc(g.label)}</div>${g.items.map((x) => `<div class="row ${x.p === mx ? 'top' : ''}"><span>${esc(x.label)}</span><span class="trk"><span class="fil" style="display:block;width:${x.p}%"></span></span><b>${fmt(x.p, 1)}%</b></div>`).join('')}</div>`;
    }).join('');
    return `<div class="blk card"><h3>${esc(bl.title)}</h3>${groups}${bl.note ? `<div class="note">${esc(bl.note)}</div>` : ''}</div>`;
  }

  const blockNote = (bl) => `<div class="blk card full">${bl.items.map((x) => `<div class="note" style="margin:0">${esc(x)}</div>`).join('')}</div>`;

  function renderBlock(bl) {
    switch (bl.type) {
      case 'cards': return blockCards(bl);
      case 'bars': return blockBars(bl);
      case 'table': return blockTable(bl);
      case 'chart': return blockChart(bl);
      case 'diff': return blockDiff(bl);
      case 'dist': return blockDist(bl);
      case 'note': return blockNote(bl);
      default: return '';
    }
  }

  function mountAll() {
    document.querySelectorAll('[data-mount]').forEach((box) => {
      const m = mounts[+box.dataset.mount];
      if (m.type === 'chart') drawChart(box, m.bl);
      if (m.type === 'spark') { box.innerHTML = ''; drawSpark(box, m.t); }
    });
  }

  function stampHTML() {
    const g = D.generatedAt.replace('T', ' ').slice(0, 16);
    const inv = D.upcoming && D.upcoming.updatedAt ? new Date(D.upcoming.updatedAt).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '–';
    return `<div class="data-stamp"><span class="stamp-label">DATA STATUS</span><strong>${esc(g)} KST</strong><span>자동 수집</span><small>컨센서스 갱신 ${esc(inv)}</small></div>`;
  }

  // ───────── 보드 페이지
  function tileHTML(t) {
    const subs = (t.subs || []).map((s) => `<span><i>${esc(s.label)}</i><b class="${dir(s.v, s.d)}">${fmt(s.v, s.d, true, s.u)}</b></span>`).join('');
    let body;
    if (t.kind === 'dist') {
      body = `<div class="t-value">${esc(t.valueLabel)}<small>${fmt(t.value, 0)}%</small></div>
        <div class="mini-dist">${t.dist.map((x) => `<div class="${x.label === t.valueLabel ? 'top' : ''}"><span>${esc(x.label)}</span><span class="trk"><span class="fil" style="display:block;width:${x.p}%"></span></span><b>${fmt(x.p, 0)}%</b></div>`).join('')}</div>`;
    } else {
      const id = mounts.push({ type: 'spark', t }) - 1;
      body = `<div class="t-value">${fmt(t.value, t.d, t.u === 'k' && t.label.includes('증감'), t.u)}</div><div class="t-subs">${subs}</div><div class="spark" data-mount="${id}"></div>`;
    }
    return `<div class="tile card"><div class="t-head"><span class="t-label">${esc(t.label)}</span><span class="t-period">${esc(t.period || '')}</span></div>${body}
      <div class="t-foot">${t.note ? `<span class="warn" title="${esc(t.note)}">${esc(t.note)}</span>` : `<a href="${esc(t.url)}" target="_blank" rel="noopener" title="${esc(t.src)}">${esc(t.src)}</a>`}</div></div>`;
  }

  function calendarHTML() {
    const now = D.generatedAt.slice(0, 16).replace('T', ' ');
    let last = '', out = '';
    D.calendar.forEach((e) => {
      const day = e.kst.slice(0, 10);
      if (day !== last) { out += `<div class="cal-day">${day.slice(5).replace('-', '/')} (${e.dow})</div>`; last = day; }
      const fx = [e.forecast ? `예상 ${esc(e.forecast)}` : '', e.previous ? `이전 ${esc(e.previous)}` : ''].filter(Boolean).join(' · ');
      out += `<div class="cal-row ${e.kst < now ? 'past' : ''}"><span class="tm">${e.kst.slice(11)}</span><span class="cty ${e.impact}">${e.country}</span><span class="ev"><b>${esc(e.title)}</b><small>${fx || '&nbsp;'}</small></span></div>`;
    });
    return `<div class="card side-card"><h3>이번 주 주요 일정</h3><div class="sub">KST · ForexFactory 컨센서스 · 흐린 행 = 발표 완료</div>${out}</div>`;
  }

  function upcomingHTML() {
    const U = D.upcoming;
    if (!U || !U.items || !U.items.length) return '';
    const unitFmt = (v, e) => v == null ? null : (e.unit === 'K' ? fmt(v, e.d) + 'K' : fmt(v, e.d, false, e.unit === '%' ? '%' : ''));
    let last = '', out = '';
    U.items.forEach((e) => {
      const day = e.kst.slice(0, 10);
      if (day !== last) { out += `<div class="cal-day">${day.slice(5).replace('-', '/')} (${e.dow}) <span class="dday">${e.dday <= 0 ? '오늘' : 'D-' + e.dday}</span></div>`; last = day; }
      const f = unitFmt(e.forecast, e);
      out += `<div class="up-row"><span class="tm">${e.kst.slice(11)}</span><span class="ev"><b>${esc(e.name)}</b><small>${e.ref ? esc(e.ref) + ' · ' : ''}이전 ${unitFmt(e.previous, e) || '–'}</small></span>
        <span class="fc ${f ? '' : 'wait'}">${f ? `<i>예상</i>${f}` : '예상치 대기'}</span></div>`;
    });
    const upd = U.updatedAt ? new Date(U.updatedAt).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
    return `<div class="card side-card"><h3>다음 발표 · 컨센서스</h3><div class="sub">Investing.com 이벤트 페이지 · 갱신 ${esc(upd)} · 예상치는 보통 발표 수일 전 게시</div>${out}</div>`;
  }

  function recentHTML() {
    return `<div class="card side-card recent"><h3>최근 지표 브리핑</h3><div class="sub">발표 → 세부 분해 → 시장 반응</div>${D.briefings.map((b) =>
      `<a href="#briefing/${b.id}"><b>${esc(b.title)} · ${esc(b.periodLabel)}</b><small>${esc(b.summary[0] || '')}</small></a>`).join('')}</div>`;
  }

  function renderBoard() {
    const n = D.board.reduce((a, g) => a + g.tiles.length, 0);
    main.innerHTML = `<div class="page-head"><div><div class="eyebrow">MACRO <span class="slash">/</span> BOARD</div><h1>매크로 보드</h1>
      <p>미국·한국 핵심 지표 ${n}개 — 최신값, 변화(일간은 1주·1개월, 월간은 전월), 추세(일간 6개월·월간 24개월). 출처를 누르면 원자료로 이동.</p></div>
      ${stampHTML()}</div>
      <div class="chips jump-chips">${D.board.map((g, i) => `<button data-jump="${i}">${esc(g.name)}</button>`).join('')}</div>
      <div class="board-layout"><div>${D.board.map((g, i) => `<section class="group" id="grp-${i}"><div class="group-head"><h2>${esc(g.name)}</h2><small>${esc(g.desc)}</small></div>
      <div class="tiles">${g.tiles.map(tileHTML).join('')}</div></section>`).join('')}</div>
      <aside>${upcomingHTML()}${calendarHTML()}${recentHTML()}</aside></div>
      <div class="footer">생성 ${esc(D.generatedAt)} · 수집 ${esc(D.collectedAt || '')} (UTC) · 숫자는 collectors/macro/build.py 에서 계산. 색: 빨강 = 상승, 파랑 = 하락(좋고 나쁨이 아님).</div>`;
    main.querySelectorAll('[data-jump]').forEach((b) => b.addEventListener('click', () => document.getElementById('grp-' + b.dataset.jump).scrollIntoView({ behavior: 'smooth', block: 'start' })));
  }

  // ───────── 브리핑 페이지
  function renderBriefing(id) {
    const list = D.briefings;
    const b = list.find((x) => x.id === id) || list.find((x) => x.tier === 1) || list[0];
    const rel = b.release ? `${b.releaseNote || '발표'} ${b.release}${b.releaseTime ? ' ' + b.releaseTime : ''}` : '';
    main.innerHTML = `<div class="page-head"><div><div class="eyebrow">MACRO <span class="slash">/</span> BRIEFING</div><h1>지표 브리핑</h1>
      <p>발표가 나오면 헤드라인 → 컨센서스 대비 → 세부 분해(기여도·품목/업종) → 추세 → 시장 반응 순으로 자동 정리.</p></div>${stampHTML()}</div>
      <div class="brief-layout"><nav class="brief-list">${list.map((x) => `<a class="bl-item ${x.id === b.id ? 'active' : ''}" href="#briefing/${x.id}">
        <div class="bl-top"><b><span class="region">${x.region}</span>${esc(x.title)}</b><span class="tier ${x.tier === 1 ? 't1' : ''}">${x.tier === 1 ? '세부 분석' : '요약 카드'}</span></div>
        <div class="bl-meta">${esc(x.periodLabel)} · ${esc(x.releaseNote || '발표')} ${esc(x.release || '–')}</div><div class="bl-sum">${esc(x.summary[0] || '')}</div></a>`).join('')}</nav>
      <section><div class="card brief-head"><div class="eyebrow">${b.region === 'US' ? '미국' : '한국'} · ${b.tier === 1 ? 'TIER 1 세부 분석' : 'TIER 3 요약 카드'}</div>
        <h2>${esc(b.title)} — ${esc(b.periodLabel)}</h2><div class="meta">${esc(rel)}</div>
        <ul class="summary">${b.summary.filter(Boolean).map((s) => `<li>${esc(s)}</li>`).join('')}</ul></div>
        <div class="blocks">${b.blocks.map(renderBlock).join('')}</div>
        <div class="sources">출처: ${b.sources.map((s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a>`).join('')}</div></section></div>`;
  }

  // ───────── 라우팅
  function route() {
    mounts.length = 0; hideTip();
    const [page, id] = (location.hash.slice(1) || 'board').split('/');
    document.querySelectorAll('.main-nav button').forEach((b) => b.classList.toggle('active', b.dataset.page === page));
    const crumb = document.getElementById('crumb');
    if (crumb) crumb.textContent = page === 'briefing' ? '지표 브리핑' : '매크로 보드';
    // 상위 대시보드(iframe 부모)에 현재 하위 탭을 알려 상단 메뉴 활성 상태를 맞춘다
    if (window.parent !== window) window.parent.postMessage({ type: 'macro-page', page: page === 'briefing' ? 'brief' : 'board' }, '*');
    if (page === 'briefing') renderBriefing(id); else renderBoard();
    requestAnimationFrame(mountAll);
    window.scrollTo(0, 0);
  }
  document.querySelectorAll('.main-nav button').forEach((b) => b.addEventListener('click', () => { location.hash = b.dataset.page; }));
  const asofEl = document.getElementById('asof'), sideEl = document.getElementById('sideAsof');
  if (asofEl) asofEl.textContent = `생성 ${D.generatedAt.replace('T', ' ').slice(0, 16)} KST`;
  if (sideEl) sideEl.textContent = `데이터 ${D.generatedAt.slice(0, 10)}`;
  let rt;
  window.addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(mountAll, 150); });
  window.addEventListener('hashchange', route);
  route();
})();
