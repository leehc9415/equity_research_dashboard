import { installMarketCapture } from './market-capture.js?v=20261103-trim';

// Keep Market Charts isolated from the export and calendar styles and routes.
export function mountMarkets(root) {
  if (root.querySelector('.market-frame')) return;
  root.innerHTML = '<iframe class="market-frame" title="증시데이터 · Market Charts" src="markets/index.html#kr"></iframe>';
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
