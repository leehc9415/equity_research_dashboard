import { installMarketCapture } from './market-capture.js?v=20261103-subnav';

// 화면 파일을 바꿀 때마다 올려서 브라우저 캐시를 피한다 (iframe 페이지는 버전 없이 열면 최대 10분 예전 화면이 보임)
const MARKETS_VERSION = '20261104-sc2';
const TITLES = {kr: '차트북', rs: '상대강도', nh: '신고가', cs: '차트 셋업', sc: '종목차트'};

// Keep the market pages (차트북, 상대강도, 신고가) isolated from the export and calendar styles and routes.
// sub: 'kr' = 차트북, 'rs' = 상대강도, 'nh' = 신고가 (증시데이터 하위 메뉴에서 선택)
export function mountMarkets(root, sub = 'kr') {
  if (!TITLES[sub]) sub = 'kr';
  const existing = root.querySelector('.market-frame');
  if (existing) {
    const win = existing.contentWindow, cur = (win.location.hash || '#kr').slice(1).split('/')[0];
    const curSub = TITLES[cur] && cur !== 'kr' ? cur : 'kr';
    if (curSub !== sub) win.location.hash = '#' + sub;
    existing.title = `증시데이터 · ${TITLES[sub]}`;
    return;
  }
  root.innerHTML = `<iframe class="market-frame" title="증시데이터 · ${TITLES[sub]}" src="markets/index.html?v=${MARKETS_VERSION}#${sub}"></iframe>`;
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
