/* Investing.com 경제지표 컨센서스 추출 (브라우저에서 실행)
 *
 * 왜 브라우저인가: kr.investing.com 은 스크립트 요청(curl/urllib)을 403 으로 막는다.
 * 차단을 우회하지 않고, 사용자가 쓰는 실제 브라우저에서 이벤트 페이지를 열어 읽는다.
 *
 * 사용: kr.investing.com 아무 페이지에서 개발자도구 콘솔에 붙여넣거나 Claude in Chrome 으로 실행.
 *   → 이벤트 페이지(/economic-calendar/x-{id})의 __NEXT_DATA__ 에서 발표 이력(실제·예측·이전)을 읽어
 *   → investing_consensus_YYYYMMDD_HHMM.json 을 다운로드 폴더에 저장.
 *   → python collectors/macro/collect.py --only investing --investing <다운로드 폴더 또는 파일>
 *
 * 요청 간격 8초(4초 간격 10건 남짓에서 429 + Cloudflare 확인 화면이 떴음), 429면 60초 쉬고 재시도. 22개 ≈ 3분.
 */
(async function investingConsensus(ids, opts) {
  opts = Object.assign({ keep: 36, gapMs: 8000, download: true }, opts || {});
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const out = { source: 'kr.investing.com', fetchedAt: new Date().toISOString(), events: {} };
  const pick = (o) => {
    const r = {};
    for (const k of ['occurrence_time', 'reference_period', 'actual', 'forecast', 'previous', 'previous_revised_from',
                     'preliminary', 'actual_to_forecast', 'revised_to_previous']) if (o[k] !== undefined) r[k] = o[k];
    return r;
  };
  for (const id of ids) {
    let rec = null;
    for (let attempt = 0; attempt < 4 && !rec; attempt++) {
      try {
        const res = await fetch(`/economic-calendar/x-${id}`, { credentials: 'include' });
        if (res.status === 429) { await sleep(60000); continue; }
        if (!res.ok) { rec = { error: res.status }; break; }
        const html = await res.text();
        const m = html.match(/<script id="__NEXT_DATA__" type="application\/json">([\s\S]*?)<\/script>/);
        if (!m) { rec = { error: 'no __NEXT_DATA__' }; break; }
        const st = JSON.parse(m[1]).props.pageProps.state.economicCalendarEventStore || {};
        const ev = st.event || {};
        const occ = (st.occurrences || []).slice(0, opts.keep).map(pick);
        const first = (st.occurrences || [])[0] || {};
        rec = { name: ev.short_name, long: ev.long_name, suffix: ev.event_cycle_suffix, currency: ev.currency,
                importance: ev.importance, unit: first.unit, precision: first.precision,
                next: st.closestOccurrences && st.closestOccurrences.next_release ? pick(st.closestOccurrences.next_release) : null,
                latest: st.closestOccurrences && st.closestOccurrences.latest_release ? pick(st.closestOccurrences.latest_release) : null,
                occurrences: occ };
      } catch (e) { rec = { error: String(e).slice(0, 120) }; }
    }
    out.events[id] = rec || { error: 'rate-limited' };
    await sleep(opts.gapMs);
  }
  if (opts.download) {
    const stamp = new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 16).replace(/[-:]/g, '').replace('T', '_');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([JSON.stringify(out)], { type: 'application/json' }));
    a.download = `investing_consensus_${stamp}.json`;
    document.body.appendChild(a); a.click(); a.remove();
  }
  window.__investingConsensus = out;
  return Object.entries(out.events).map(([id, e]) => `${id}:${e.error ? 'ERR ' + e.error : (e.next && e.next.forecast != null ? 'F' : '-')}`).join(' ');
})(
  // collectors/macro/config.py 의 INVESTING_EVENTS 키와 같게 유지
  [69, 733, 56, 736, 227, 300, 8, 1777, 1, 294, 522, 256, 63, 238, 62, 61, 905, 173, 176, 375, 168, 320]
);
