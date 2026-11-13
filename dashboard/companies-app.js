// 기업데이터 하위 화면: 내부자 거래(insider_tracker 화면을 iframe으로) · 기업 리서치(준비 중)
// 화면 파일을 바꿀 때마다 올려서 브라우저 캐시를 피한다
const INSIDER_VERSION = '20261113-insider2';
export const COMPANY_SUBS = ['insider', 'research'];
const TITLES = {insider: '내부자 거래', research: '기업 리서치'};

export function mountCompanies(root, sub = 'insider') {
  if (!TITLES[sub]) sub = 'insider';
  if (sub === 'research') {
    root.innerHTML = `<div class="card module-placeholder"><div><div class="icon">↗</div><h1>기업 리서치</h1><p>준비 중인 화면입니다.</p></div></div>`;
    return;
  }
  if (root.querySelector('.insider-frame')) return;
  root.innerHTML = `<iframe class="market-frame insider-frame" title="기업데이터 · 내부자 거래" src="insider/trades.html?v=${INSIDER_VERSION}"></iframe>`;
  const header = document.querySelector('.site-header');
  const resize = () => root.style.setProperty('--market-header-height', `${header.getBoundingClientRect().height}px`);
  resize();
  if (!header.companyResizeObserver) {
    header.companyResizeObserver = new ResizeObserver(resize);
    header.companyResizeObserver.observe(header);
  }
}
