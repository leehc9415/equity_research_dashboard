const rendererUrl = new URL('./vendor/html2canvas-1.4.1.min.js', import.meta.url).href;

function loadRenderer(doc) {
  if (doc.defaultView.html2canvas) return Promise.resolve(doc.defaultView.html2canvas);
  return new Promise((resolve, reject) => {
    const script = doc.createElement('script');
    script.src = rendererUrl;
    script.onload = () => resolve(doc.defaultView.html2canvas);
    script.onerror = () => { script.remove(); reject(new Error('PNG 도구를 불러오지 못했습니다.')); };
    doc.head.appendChild(script);
  });
}

async function renderPng(doc) {
  const win = doc.defaultView;
  // Freeze the crop before fonts or the renderer finish loading.
  const x = win.scrollX, y = win.scrollY;
  const width = doc.documentElement.clientWidth, height = win.innerHeight;
  const headerHeight = doc.querySelector('header').getBoundingClientRect().height;
  const renderer = await loadRenderer(doc);
  await doc.fonts.ready;
  const canvas = await renderer(doc.body, {
    x, y, width, height, scrollX: x, scrollY: y,
    windowWidth: win.innerWidth, windowHeight: height,
    scale: Math.min(win.devicePixelRatio || 1, 2),
    backgroundColor: win.getComputedStyle(doc.body).backgroundColor,
    logging: false,
    onclone: clone => {
      // Preserve the sticky toolbar at the top of a scrolled capture.
      const header = clone.querySelector('header');
      Object.assign(header.style, {position: 'absolute', top: `${y}px`, left: '0', width: '100%'});
      const spacer = clone.createElement('div');
      spacer.style.height = `${headerHeight}px`;
      header.after(spacer);
      // Inline SVGs become images during rendering; resolve inherited colors
      // and fonts so chart paths and labels survive that conversion.
      clone.querySelectorAll('svg, svg *').forEach(node => {
        const style = clone.defaultView.getComputedStyle(node);
        for (const property of ['fill', 'stroke', 'stroke-width', 'font-family', 'font-size', 'font-weight', 'color']) {
          node.style.setProperty(property, style.getPropertyValue(property));
        }
      });
    },
  });
  return new Promise((resolve, reject) => canvas.toBlob(blob => {
    if (blob) resolve(blob); else reject(new Error('PNG를 만들지 못했습니다.'));
  }, 'image/png'));
}

export function installMarketCapture(frame) {
  const doc = frame.contentDocument, win = frame.contentWindow;
  const toolbar = doc?.querySelector('.bar');
  if (!toolbar || toolbar.querySelector('.capture-actions')) return;
  const style = doc.createElement('style');
  style.textContent = `
    .capture-actions{display:flex;gap:5px;align-items:center;position:relative}
    .capture-actions button,.capture-actions a{border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);font:inherit;font-size:12px;font-weight:600;padding:5px 10px;cursor:pointer;white-space:nowrap;text-decoration:none}
    .capture-actions button:hover,.capture-actions a:hover{border-color:var(--ink)}
    .capture-actions button:focus-visible,.capture-actions a:focus-visible{outline:2px solid var(--ma);outline-offset:2px}
    .capture-actions button:disabled{opacity:.6;cursor:wait}
    .capture-actions [hidden]{display:none}
    .capture-status{position:absolute;right:0;top:calc(100% + 6px);width:max-content;max-width:min(320px,85vw);white-space:normal;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:7px 10px;font-size:12px;box-shadow:0 3px 12px #0002;z-index:10}
    .capture-status:empty{display:none}
  `;
  doc.head.appendChild(style);
  const actions = doc.createElement('div');
  actions.className = 'capture-actions';
  actions.setAttribute('data-html2canvas-ignore', 'true');
  actions.innerHTML = '<button type="button" class="capture-copy" title="현재 보이는 증시데이터 화면을 PNG 이미지로 복사합니다">PNG 복사</button><a class="capture-save" hidden download>PNG 저장</a><span class="capture-status" role="status" aria-live="polite"></span>';
  toolbar.appendChild(actions);
  const copy = actions.querySelector('.capture-copy');
  const save = actions.querySelector('.capture-save');
  const status = actions.querySelector('.capture-status');
  let lastBlob = null, downloadUrl = null, clearStatus;
  const releaseUrl = () => { if (downloadUrl) win.URL.revokeObjectURL(downloadUrl); downloadUrl = null; };
  win.addEventListener('pagehide', releaseUrl, {once: true});
  function announce(text, clear = false) {
    win.clearTimeout(clearStatus);
    status.textContent = text;
    if (clear) clearStatus = win.setTimeout(() => { status.textContent = ''; }, 5000);
  }
  function withTimeout(promise, milliseconds) {
    let timer;
    return Promise.race([promise, new Promise((_, reject) => {
      timer = win.setTimeout(() => reject(new Error('응답 시간이 초과되었습니다.')), milliseconds);
    })]).finally(() => win.clearTimeout(timer));
  }
  copy.addEventListener('click', async () => {
    if (copy.disabled) return;
    copy.disabled = true;
    save.hidden = true;
    lastBlob = null;
    releaseUrl();
    announce('PNG를 만드는 중…');
    const png = withTimeout(renderPng(doc), 15000).then(blob => {
      lastBlob = blob;
      downloadUrl = win.URL.createObjectURL(blob);
      save.href = downloadUrl;
      save.download = `market-charts-${win.location.hash.slice(1).replace(/[^a-z0-9_-]/gi, '-') || 'kr'}.png`;
      save.hidden = false;
      announce('PNG 생성 완료 · 클립보드로 복사 중…');
      return blob;
    });
    try {
      if (!win.navigator.clipboard?.write || !win.ClipboardItem) {
        await png;
        save.hidden = false;
        announce('이 브라우저에서는 PNG 저장을 이용해 주세요.');
        return;
      }
      // Start clipboard.write inside the click gesture. Safari requires this
      // even when generating the PNG takes longer than the activation window.
      await withTimeout(win.navigator.clipboard.write([new win.ClipboardItem({'image/png': png})]), 15000);
      announce('PNG 복사 완료 · 원하는 곳에 붙여넣으세요.', true);
    } catch {
      await png.catch(() => {});
      if (lastBlob) {
        save.hidden = false;
        announce('복사가 허용되지 않았습니다. PNG 저장을 이용해 주세요.');
      } else {
        announce('PNG를 만들지 못했습니다. 다시 눌러 주세요.');
      }
    } finally {
      copy.disabled = false;
      save.hidden = !lastBlob;
    }
  });
  save.addEventListener('click', () => {
    if (!lastBlob) return;
    announce('PNG 파일 저장을 요청했습니다.', true);
  });
}
