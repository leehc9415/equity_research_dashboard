// 시황/매크로 하위 화면: 매크로 보드 · 지표 브리핑 (macro/index.html 을 iframe으로)
// 화면 파일을 바꿀 때마다 올려서 브라우저 캐시를 피한다
const MACRO_VERSION = '20261120-macro1';
export const MACRO_SUBS = ['board', 'brief'];
const TITLES = {board: '매크로 보드', brief: '지표 브리핑'};
const INNER = {board: 'board', brief: 'briefing'};

export function mountMacro(root, sub = 'board') {
  if (!TITLES[sub]) sub = 'board';
  const existing = root.querySelector('.macro-frame');
  if (existing) {
    const win = existing.contentWindow, cur = (win.location.hash || '#board').slice(1).split('/')[0];
    if (cur !== INNER[sub]) win.location.hash = '#' + INNER[sub];
    existing.title = `시황/매크로 · ${TITLES[sub]}`;
    return;
  }
  root.innerHTML = `<iframe class="market-frame macro-frame" title="시황/매크로 · ${TITLES[sub]}" src="macro/index.html?v=${MACRO_VERSION}#${INNER[sub]}"></iframe>`;
  const header = document.querySelector('.site-header');
  const resize = () => root.style.setProperty('--market-header-height', `${header.getBoundingClientRect().height}px`);
  resize();
  if (!header.macroResizeObserver) {
    header.macroResizeObserver = new ResizeObserver(resize);
    header.macroResizeObserver.observe(header);
  }
}
