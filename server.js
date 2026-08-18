const http = require('http');
const https = require('https');
const { URL } = require('url');

const PORT = 3000;
const TARGET_BASE = 'https://sugang.cju.ac.kr';

// Search widget to inject into HTML pages
const SEARCH_WIDGET = `
<style>
  #_sw_ {
    position: fixed; top: 0; left: 0; right: 0; z-index: 999999;
    background: #1a1f2e; border-bottom: 1px solid rgba(126,163,247,0.15);
    padding: 8px 16px; display: flex; align-items: center; gap: 10px;
    font-family: 'Segoe UI', -apple-system, sans-serif; box-shadow: 0 4px 24px rgba(0,0,0,0.4);
  }
  #_sw_ input {
    flex: 1; max-width: 400px; padding: 8px 14px; border-radius: 8px;
    border: 1px solid rgba(126,163,247,0.2); background: #0d1117; color: #e0e4ef;
    font-size: 14px; outline: none; font-family: inherit;
  }
  #_sw_ input:focus { border-color: rgba(126,163,247,0.5); }
  #_sw_ input::placeholder { color: #5a6380; }
  #_sw_ button {
    padding: 6px 14px; border-radius: 6px; border: 1px solid rgba(255,255,255,0.08);
    background: rgba(126,163,247,0.1); color: #7ea3f7; cursor: pointer;
    font-size: 13px; font-family: inherit; transition: background 0.15s;
  }
  #_sw_ button:hover { background: rgba(126,163,247,0.2); }
  #_sw_ .info { color: #8a92a8; font-size: 12px; min-width: 80px; text-align: center; }
  #_sw_ .close-btn { background: rgba(248,113,113,0.1); color: #f87171; border-color: rgba(248,113,113,0.15); }
  #_sw_ .close-btn:hover { background: rgba(248,113,113,0.2); }
  ._hl_ { background: rgba(251,191,36,0.35) !important; outline: 2px solid rgba(251,191,36,0.6); border-radius: 2px; }
  ._hl_cur_ { background: rgba(126,163,247,0.5) !important; outline: 2px solid rgba(126,163,247,0.8); }
  body { padding-top: 52px !important; }
</style>
<div id="_sw_">
  <input id="_si_" type="text" placeholder="검색어를 입력하세요..." autofocus />
  <button onclick="_sprev()">◀ 이전</button>
  <button onclick="_snext()">다음 ▶</button>
  <span class="info" id="_sc_">0 / 0</span>
  <button class="close-btn" onclick="document.getElementById('_sw_').style.display='none'">✕</button>
</div>
<script>
(function(){
  let marks = [], cur = -1;
  const inp = document.getElementById('_si_');
  const cnt = document.getElementById('_sc_');
  let debounce;

  inp.addEventListener('input', function() {
    clearTimeout(debounce);
    debounce = setTimeout(() => doSearch(inp.value.trim()), 200);
  });

  inp.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') { e.shiftKey ? _sprev() : _snext(); }
    if (e.key === 'Escape') {
      clearHighlights();
      inp.value = '';
      cnt.textContent = '0 / 0';
    }
  });

  function doSearch(q) {
    clearHighlights();
    cur = -1;
    if (!q) { cnt.textContent = '0 / 0'; return; }

    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode: function(node) {
        if (node.parentElement && (node.parentElement.id === '_sw_' || node.parentElement.closest('#_sw_'))) return NodeFilter.FILTER_REJECT;
        if (node.parentElement && ['SCRIPT','STYLE','NOSCRIPT'].includes(node.parentElement.tagName)) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });

    const textNodes = [];
    let n;
    while (n = walker.nextNode()) textNodes.push(n);

    const lq = q.toLowerCase();
    textNodes.forEach(node => {
      const text = node.textContent;
      const lt = text.toLowerCase();
      let idx = lt.indexOf(lq);
      if (idx === -1) return;

      const frag = document.createDocumentFragment();
      let lastIdx = 0;
      while (idx !== -1) {
        if (idx > lastIdx) frag.appendChild(document.createTextNode(text.slice(lastIdx, idx)));
        const mark = document.createElement('mark');
        mark.className = '_hl_';
        mark.textContent = text.slice(idx, idx + q.length);
        frag.appendChild(mark);
        marks.push(mark);
        lastIdx = idx + q.length;
        idx = lt.indexOf(lq, lastIdx);
      }
      if (lastIdx < text.length) frag.appendChild(document.createTextNode(text.slice(lastIdx)));
      node.parentNode.replaceChild(frag, node);
    });

    cnt.textContent = marks.length > 0 ? '0 / ' + marks.length : '결과 없음';
    if (marks.length > 0) { cur = 0; goTo(0); }
  }

  function goTo(i) {
    marks.forEach(m => m.classList.remove('_hl_cur_'));
    if (marks[i]) {
      marks[i].classList.add('_hl_cur_');
      marks[i].scrollIntoView({ behavior: 'smooth', block: 'center' });
      cnt.textContent = (i + 1) + ' / ' + marks.length;
    }
  }

  window._snext = function() {
    if (marks.length === 0) return;
    cur = (cur + 1) % marks.length;
    goTo(cur);
  };

  window._sprev = function() {
    if (marks.length === 0) return;
    cur = (cur - 1 + marks.length) % marks.length;
    goTo(cur);
  };

  function clearHighlights() {
    marks.forEach(m => {
      const parent = m.parentNode;
      if (parent) {
        parent.replaceChild(document.createTextNode(m.textContent), m);
        parent.normalize();
      }
    });
    marks = [];
  }
})();
</script>
`;

function proxyRequest(targetUrl, res) {
  const parsed = new URL(targetUrl);
  
  const options = {
    hostname: parsed.hostname,
    port: parsed.port || 443,
    path: parsed.pathname + parsed.search,
    method: 'GET',
    headers: {
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
      'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
      'Accept-Language': 'ko-KR,ko;q=0.9,en;q=0.8',
      'Accept-Encoding': 'identity',
      'Referer': TARGET_BASE + '/',
    },
    rejectUnauthorized: false,
  };

  const proxyReq = https.request(options, (proxyRes) => {
    const contentType = proxyRes.headers['content-type'] || '';
    const chunks = [];

    proxyRes.on('data', chunk => chunks.push(chunk));
    proxyRes.on('end', () => {
      const buffer = Buffer.concat(chunks);

      if (contentType.includes('text/html')) {
        let html = buffer.toString('utf-8');

        // Rewrite absolute URLs to proxy through our server
        html = html.replace(/(href|src|action)=["']\//g, `$1="/proxy/`);
        html = html.replace(/(href|src|action)=["']https:\/\/sugang\.cju\.ac\.kr\//g, `$1="/proxy/`);

        // Inject search widget before </body>
        if (html.includes('</body>')) {
          html = html.replace('</body>', SEARCH_WIDGET + '</body>');
        } else {
          html += SEARCH_WIDGET;
        }

        res.writeHead(proxyRes.statusCode, {
          'Content-Type': 'text/html; charset=utf-8',
          'Cache-Control': 'no-cache',
        });
        res.end(html);
      } else {
        // Pass through non-HTML content (CSS, JS, images, etc.)
        const headers = {};
        if (contentType) headers['Content-Type'] = contentType;
        headers['Cache-Control'] = 'public, max-age=3600';
        res.writeHead(proxyRes.statusCode, headers);
        res.end(buffer);
      }
    });
  });

  proxyReq.on('error', (err) => {
    console.error('Proxy error:', err.message);
    res.writeHead(502, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('프록시 오류: ' + err.message);
  });

  proxyReq.end();
}

// Landing page
const LANDING_HTML = `<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>청주대 수강신청 검색</title>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&display=swap" rel="stylesheet">
  <style>
    * { margin:0; padding:0; box-sizing:border-box; }
    body { font-family:'Noto Sans KR',sans-serif; background:#0a0e1a; color:#e0e4ef;
           min-height:100vh; display:flex; align-items:center; justify-content:center; }
    .wrap { text-align:center; padding:2rem; }
    h1 { font-size:1.4rem; font-weight:700; color:#f0f2f8; margin-bottom:0.5rem; }
    h1 span { color:#7ea3f7; }
    p { font-size:0.85rem; color:#5a6380; margin-bottom:2rem; }
    a {
      display:inline-block; padding:1rem 2.5rem; border-radius:10px;
      background:rgba(126,163,247,0.12); color:#7ea3f7; text-decoration:none;
      border:1px solid rgba(126,163,247,0.15); font-weight:500; font-size:0.95rem;
      transition:background 0.2s;
    }
    a:hover { background:rgba(126,163,247,0.2); }
    .hint { margin-top:1.5rem; font-size:0.72rem; color:#3b4a6b; }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>청주대 <span>수강신청</span> 페이지 검색</h1>
    <p>페이지 상단에 검색바가 표시됩니다. 검색어를 입력하면 자동으로 하이라이트됩니다.</p>
    <a href="/proxy/common/frame.do?method=redirect&UID=6a011048:19feb640a8e:-75cd">수강신청 페이지 열기 →</a>
    <div class="hint">Enter = 다음 결과 | Shift+Enter = 이전 결과 | Esc = 초기화</div>
  </div>
</body>
</html>`;

const server = http.createServer((req, res) => {
  const reqUrl = new URL(req.url, `http://localhost:${PORT}`);

  if (reqUrl.pathname === '/' || reqUrl.pathname === '') {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end(LANDING_HTML);
    return;
  }

  if (reqUrl.pathname.startsWith('/proxy/')) {
    const targetPath = reqUrl.pathname.replace('/proxy', '') + reqUrl.search;
    const targetUrl = TARGET_BASE + targetPath;
    console.log('Proxying:', targetUrl);
    proxyRequest(targetUrl, res);
    return;
  }

  // Fallback: try to proxy as-is
  const targetUrl = TARGET_BASE + reqUrl.pathname + reqUrl.search;
  console.log('Proxying (fallback):', targetUrl);
  proxyRequest(targetUrl, res);
});

server.listen(PORT, () => {
  console.log('');
  console.log('  ✦ 청주대 수강신청 검색 서버 실행 중');
  console.log('  ➜ http://localhost:' + PORT);
  console.log('');
});
