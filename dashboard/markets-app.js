import { installMarketCapture } from './market-capture.js?v=20261103-subnav';

// Keep the market pages (Chart Book, RS 랭킹) isolated from the export and calendar styles and routes.
// sub: 'kr' = Chart Book, 'rs' = RS 랭킹 (증시데이터 하위 메뉴에서 선택)
export function mountMarkets(root, sub = 'kr') {
  const existing = root.querySelector('.market-frame');
  if (existing) {
    const win = existing.contentWindow, cur = (win.location.hash || '#kr').slice(1).split('/')[0];
    const isRs = cur === 'rs';
    if (sub === 'rs' && !isRs) win.location.hash = '#rs';
    if (sub !== 'rs' && isRs) win.location.hash = '#kr';
    return;
  }
  root.innerHTML = `<iframe class="market-frame" title="증시데이터 · ${sub === 'rs' ? 'RS 랭킹' : 'Chart Book'}" src="markets/index.html#${sub === 'rs' ? 'rs' : 'kr'}"></iframe>`;
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
