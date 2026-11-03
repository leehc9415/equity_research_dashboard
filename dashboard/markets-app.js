import { installMarketCapture } from './market-capture.js?v=20261103-subnav';

// 화면 파일을 바꿀 때마다 올려서 브라우저 캐시를 피한다 (iframe 페이지는 버전 없이 열면 최대 10분 예전 화면이 보임)
const MARKETS_VERSION = '20261103-3';

// Keep the market pages (Chart Book, RS 랭킹) isolated from the export and calendar styles and routes.
// sub: 'kr' = 차트북, 'rs' = 상대강도 (증시데이터 하위 메뉴에서 선택)
export function mountMarkets(root, sub = 'kr') {
  const existing = root.querySelector('.market-frame');
  if (existing) {
    const win = existing.contentWindow, cur = (win.location.hash || '#kr').slice(1).split('/')[0];
    const isRs = cur === 'rs';
    if (sub === 'rs' && !isRs) win.location.hash = '#rs';
    if (sub !== 'rs' && isRs) win.location.hash = '#kr';
    return;
  }
  root.innerHTML = `<iframe class="market-frame" title="증시데이터 · ${sub === 'rs' ? '상대강도' : '차트북'}" src="markets/index.html?v=${MARKETS_VERSION}#${sub === 'rs' ? 'rs' : 'kr'}"></iframe>`;
  const frame = root.querySelector('.market-frame');
  frame.addEventListener('load', () => installMarketCapture(frame));
  const header = document.querySelector('.site-header');
  const resize = () => root.style.setProperty('--market-header-height', `${header.getBoundingClientRect().height}px`);
  resize();
  if (!header.marketResizeObserver) {
    header.marketResizeObserver = new ResizeObserver(resize);
    header.marketResizeObserver.observe(header);
  }
}
