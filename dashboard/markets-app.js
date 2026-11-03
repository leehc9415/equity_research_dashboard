// Keep Market Charts isolated from the export and calendar styles and routes.
export function mountMarkets(root) {
  if (root.querySelector('.market-frame')) return;
  root.innerHTML = '<iframe class="market-frame" title="증시데이터 · Market Charts" src="markets/index.html#kr"></iframe>';
  const header = document.querySelector('.site-header');
  const resize = () => root.style.setProperty('--market-header-height', `${header.getBoundingClientRect().height}px`);
  resize();
  if (!header.marketResizeObserver) {
    header.marketResizeObserver = new ResizeObserver(resize);
    header.marketResizeObserver.observe(header);
  }
}
