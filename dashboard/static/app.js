/* ── AI Job Agent Dashboard — Real-time JS Engine ────────────────────────── */

'use strict';

// ── Toast ─────────────────────────────────────────────────────────────────────

let toastTimer = null;
function showToast(msg, type = 'info', icon = '📢') {
  const el = document.getElementById('toast');
  if (!el) return;
  el.innerHTML = `<span>${icon}</span> ${msg}`;
  el.className = `toast ${type}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.className = 'toast hidden', 5000);
}

// ── countUp animation ─────────────────────────────────────────────────────────

function animateCount(el, to, duration = 900) {
  if (!el) return;
  const from = parseInt(el.dataset.current || '0');
  el.dataset.current = to;
  if (from === to) { el.textContent = to; return; }
  const start = performance.now();
  const diff = to - from;
  function step(ts) {
    const progress = Math.min((ts - start) / duration, 1);
    const ease = 1 - Math.pow(1 - progress, 3); // ease-out cubic
    el.textContent = Math.round(from + diff * ease);
    if (progress < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

// ── Stats polling ─────────────────────────────────────────────────────────────

function loadStats() {
  fetch('/api/stats')
    .then(r => r.json())
    .then(d => {
      animateCount(document.getElementById('s-total'),   d.total   || 0);
      animateCount(document.getElementById('s-today'),   d.today   || 0);
      animateCount(document.getElementById('s-scored'),  d.scored  || 0);
      animateCount(document.getElementById('s-strong'),  d.strong  || 0);
      animateCount(document.getElementById('s-applied'), d.applied || 0);
      animateCount(document.getElementById('s-pending'), d.pending || 0);
      document.querySelectorAll('.stat-card.loading')
        .forEach(c => c.classList.remove('loading'));

      // Update nav counts
      const navTotal = document.getElementById('nav-count-jobs');
      if (navTotal) navTotal.textContent = d.total || 0;
    })
    .catch(() => {});
}

// ── Trending sidebar ──────────────────────────────────────────────────────────

function loadTrending() {
  const compList = document.getElementById('trending-companies');
  const srcBars  = document.getElementById('source-bars');
  const distEl   = document.getElementById('score-dist');
  if (!compList && !srcBars && !distEl) return;

  fetch('/api/trending')
    .then(r => r.json())
    .then(d => {
      // Companies
      if (compList && d.top_companies) {
        compList.innerHTML = d.top_companies.map(([name, count], i) => `
          <div class="trend-row">
            <span class="trend-rank">${i + 1}</span>
            <span class="trend-name" title="${name}">${name}</span>
            <span class="trend-count">${count}</span>
          </div>
        `).join('');
      }

      // Source bars
      if (srcBars && d.sources) {
        const total = Object.values(d.sources).reduce((a, b) => a + b, 0) || 1;
        const srcOrder = ['linkedin', 'jooble', 'adzuna'];
        srcBars.innerHTML = srcOrder.map(src => {
          const cnt = d.sources[src] || 0;
          const pct = Math.round(cnt / total * 100);
          return `
            <div class="source-bar-row">
              <div class="source-bar-label">
                <span>${src.charAt(0).toUpperCase() + src.slice(1)}</span>
                <span style="color:var(--text-3)">${cnt}</span>
              </div>
              <div class="source-bar-track">
                <div class="source-bar-fill ${src}" style="width:${pct}%"></div>
              </div>
            </div>
          `;
        }).join('');
      }

      // Score distribution
      if (distEl && d.score_dist) {
        const { strong, good, weak } = d.score_dist;
        distEl.innerHTML = `
          <div class="score-dist-item strong">
            <div class="score-dist-num">${strong}</div>
            <div class="score-dist-lbl">Strong</div>
          </div>
          <div class="score-dist-item good">
            <div class="score-dist-num">${good}</div>
            <div class="score-dist-lbl">Good</div>
          </div>
          <div class="score-dist-item weak">
            <div class="score-dist-num">${weak}</div>
            <div class="score-dist-lbl">Weak</div>
          </div>
        `;
      }
    })
    .catch(() => {});
}

// ── Live feed & ticker ────────────────────────────────────────────────────────

let _lastCount = -1;

function freshnessClass(f) {
  const m = { 'TODAY': 'today', 'NEW': 'new', 'RECENT': 'recent', 'OLD': 'old' };
  return m[f] || 'old';
}
function freshnessEmoji(f) {
  const m = { 'TODAY': '🔥', 'NEW': '⚡', 'RECENT': '✨', 'OLD': '' };
  return m[f] || '';
}

function buildTicker(jobs) {
  const inner = document.getElementById('ticker-inner');
  if (!inner || !jobs.length) return;
  const items = jobs.slice(0, 40).map(j => {
    const fCls = freshnessClass(j.freshness);
    const scoreStr = j.score !== null && j.score !== undefined
      ? `<span class="t-score">${j.score}%</span>` : '';
    return `<span class="ticker-item">
      <span class="t-fresh ${fCls}">${freshnessEmoji(j.freshness)} ${j.freshness}</span>
      <strong style="color:var(--text)">${j.title}</strong>
      <span style="color:var(--text-3)">@</span>
      <span style="color:var(--purple)">${j.company}</span>
      ${scoreStr}
    </span>`;
  }).join('');
  // Duplicate for seamless loop
  inner.innerHTML = items + items;
}

function buildFeedCard(job, rank) {
  const fCls = freshnessClass(job.freshness);
  const rankCls = rank <= 3 ? `rank-${rank}` : '';
  const rec = (job.recommendation || '').toLowerCase();
  const recCls = rec || 'unscored';
  const scoreStr = job.score !== null && job.score !== undefined ? `${job.score}` : '?';
  const dashArr = job.score !== null && job.score !== undefined ? `${job.score}, 100` : `0, 100`;
  const srcs = (job.sources || []).map(s =>
    `<span class="source-pill ${s}">${s}</span>`
  ).join('');

  return `<a href="/jobs/${job.id}" class="feed-card">
    <div class="feed-card-rank ${rankCls}">${rank}</div>
    <div class="feed-card-body">
      <div class="feed-card-top">
        <span class="fresh-pill fresh-${fCls}">${freshnessEmoji(job.freshness)} ${job.freshness}</span>
        <span class="feed-title">${job.title}</span>
      </div>
      <div class="feed-meta">
        <span class="feed-company">🏢 ${job.company}</span>
        <span class="divider">·</span>
        <span style="color:var(--text-3)">📍 ${job.location}</span>
        <span class="divider">·</span>
        ${srcs}
      </div>
      ${job.excerpt ? `<div class="feed-excerpt">${job.excerpt}</div>` : ''}
    </div>
    <div class="feed-card-right">
      <div class="score-ring ${recCls}">
        <svg viewBox="0 0 36 36">
          <path class="ring-bg" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"/>
          <path class="ring-fill" stroke-dasharray="${dashArr}"
            d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"/>
        </svg>
        <span class="score-pct">${scoreStr}%</span>
      </div>
      <div class="score-label ${recCls}">${rec || '—'}</div>
    </div>
  </a>`;
}

function loadFeed() {
  const feedEl = document.getElementById('live-feed');
  fetch('/api/feed')
    .then(r => r.json())
    .then(d => {
      const jobs = d.jobs || [];

      // Ticker (always update)
      buildTicker(jobs);

      // Feed cards (only on home page)
      if (feedEl) {
        const isFirst = _lastCount === -1;
        if (!isFirst && d.count > _lastCount) {
          const newCount = d.count - _lastCount;
          showToast(`${newCount} new job${newCount > 1 ? 's' : ''} just appeared!`, 'info', '🔥');
        }
        _lastCount = d.count;

        if (jobs.length === 0) {
          feedEl.innerHTML = `<div class="empty-state glass">
            <div class="empty-icon">🔍</div>
            <h3>No jobs yet</h3>
            <p>Run <code>python main.py</code> to fetch jobs.</p>
          </div>`;
          return;
        }

        const html = jobs.slice(0, 20).map((j, i) => {
          const card = buildFeedCard(j, i + 1);
          return isFirst ? card : card;
        }).join('');

        if (isFirst) {
          feedEl.innerHTML = html;
        } else {
          // Smooth update — only if count changed
          if (d.count !== _lastCount) feedEl.innerHTML = html;
        }
      }
    })
    .catch(() => {});
}

// ── Sidebar action buttons ────────────────────────────────────────────────────

function triggerLogin() {
  const btn = document.getElementById('btn-login');
  if (btn) { btn.classList.add('loading'); btn.textContent = '🔑 Launching…'; }
  showToast('Opening browser window — complete login/captcha there!', 'info', '🔑');

  fetch('/run/login', { method: 'POST' })
    .then(r => r.json())
    .then(d => {
      showToast('Browser opened! Complete login in the pop-up window.', 'success', '🌐');
      setTimeout(() => {
        if (btn) { btn.classList.remove('loading'); btn.innerHTML = '<span>🔑</span> Session Login'; }
      }, 15000);
    })
    .catch(() => {
      if (btn) { btn.classList.remove('loading'); btn.innerHTML = '<span>🔑</span> Session Login'; }
    });
}

function inspectApp(appId) {
  fetch(`/api/applications/${appId}`)
    .then(r => r.json())
    .then(d => {
      const modal = document.getElementById('inspect-modal');
      const content = document.getElementById('inspect-modal-content');
      if (!modal || !content) return;

      const logsHtml = (d.logs_parsed || []).map(l =>
        `<div class="log-line"><code>${l}</code></div>`
      ).join('');

      const imgHtml = d.screenshot_url
        ? `<div style="margin-top:1rem">
             <div class="detail-label">Captured Playwright Screenshot</div>
             <a href="${d.screenshot_url}" target="_blank">
               <img src="${d.screenshot_url}" style="width:100%;max-height:350px;object-fit:cover;border-radius:8px;border:1px solid var(--border)">
             </a>
           </div>`
        : `<div style="color:var(--text-3);margin-top:1rem">No screenshot recorded.</div>`;

      content.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem">
          <div>
            <h2 style="font-family:var(--font-display);font-size:18px;font-weight:700">${d.title}</h2>
            <p style="font-size:12px;color:var(--text-2)">${d.company} · Application #${d.id}</p>
          </div>
          <span class="status-badge status-${(d.status||'').toLowerCase()}">${d.status}</span>
        </div>

        ${d.error_reason ? `<div style="padding:0.75rem;background:rgba(249,115,22,0.1);border:1px solid rgba(249,115,22,0.3);border-radius:8px;font-size:12px;color:var(--orange);margin-bottom:1rem">
          <strong>Point of Stoppage:</strong> ${d.error_reason}
        </div>` : ''}

        <div style="display:flex;gap:0.5rem;margin-bottom:1rem">
          <a href="${d.job_url}" target="_blank" class="btn-filter" style="font-size:12px;padding:4px 10px;text-decoration:none">
            Open Job Posting ↗
          </a>
          <button class="btn-clear" style="font-size:12px;padding:4px 10px" onclick="triggerLogin()">
            🔑 Run Session Login
          </button>
        </div>

        <div class="detail-label" style="margin-top:1rem">Execution Logs</div>
        <div style="background:var(--bg);padding:0.85rem;border-radius:8px;max-height:200px;overflow-y:auto;font-family:var(--font-mono);font-size:11.5px;color:var(--text-2);border:1px solid var(--border)">
          ${logsHtml}
        </div>

        ${imgHtml}
      `;

      modal.classList.remove('modal-hidden');
    })
    .catch(e => {
      showToast('Error loading application log: ' + e, 'error');
    });
}

function closeInspectModal() {
  const modal = document.getElementById('inspect-modal');
  if (modal) modal.classList.add('modal-hidden');
}


function triggerFetch() {
  const btn = document.getElementById('btn-fetch');
  if (btn) { btn.classList.add('loading'); btn.textContent = '⏳ Fetching…'; }
  fetch('/run/fetch', { method: 'POST' })
    .then(() => {
      showToast('Job fetch started — check back in ~30 seconds', 'info', '🔄');
      setTimeout(() => {
        if (btn) { btn.classList.remove('loading'); btn.innerHTML = '<span>🔄</span> Fetch Jobs'; }
        loadStats(); loadFeed(); loadTrending();
      }, 10000);
    })
    .catch(() => {
      if (btn) { btn.classList.remove('loading'); btn.innerHTML = '<span>🔄</span> Fetch Jobs'; }
    });
}

function triggerScore() {

  const btn = document.getElementById('btn-score');
  if (btn) { btn.classList.add('loading'); btn.textContent = '🧠 Scoring…'; }
  fetch('/run/score', { method: 'POST' })
    .then(() => {
      showToast('AI scoring started — jobs will be scored with Claude!', 'success', '🧠');
      setTimeout(() => {
        if (btn) { btn.classList.remove('loading'); btn.innerHTML = '<span>🧠</span> Run AI Score'; }
        loadStats(); loadFeed(); loadTrending();
      }, 15000);
    })
    .catch(() => {
      if (btn) { btn.classList.remove('loading'); btn.innerHTML = '<span>🧠</span> Run AI Score'; }
    });
}

// ── Auto-apply (job detail page) ──────────────────────────────────────────────

function applyJob(jobId, dryRun) {
  const btn = document.getElementById('apply-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Applying…'; }
  showToast('Browser opening, filling form…', 'info', '🤖');

  fetch(`/api/apply/${jobId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dry_run: dryRun, headful: true }),
  })
    .then(r => r.json())
    .then(d => {
      if (btn) { btn.disabled = false; btn.textContent = '🚀 Auto-Apply'; }
      const icons = { APPLIED: '✅', DRY_RUN: '🧪', REQUIRES_MANUAL: '👆', FAILED: '❌' };
      const icon = icons[d.status] || '📋';
      showToast(`${icon} ${d.status}${d.error ? ' — ' + d.error.slice(0, 80) : ''}`,
        d.status === 'APPLIED' ? 'success' : 'info', icon);
    })
    .catch(e => {
      if (btn) { btn.disabled = false; btn.textContent = '🚀 Auto-Apply'; }
      showToast('Apply request failed — check the console', 'error', '❌');
    });
}

// ── Live clock ────────────────────────────────────────────────────────────────

function startClock() {
  const el = document.getElementById('live-time');
  if (!el) return;
  function tick() {
    el.textContent = new Date().toLocaleTimeString('en-US', {
      hour: '2-digit', minute: '2-digit', second: '2-digit'
    });
  }
  tick();
  setInterval(tick, 1000);
}

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  startClock();
  loadStats();
  loadFeed();
  loadTrending();

  // Refresh every 30 seconds
  setInterval(() => { loadStats(); loadFeed(); }, 30000);
  // Trending every 60 seconds
  setInterval(() => { loadTrending(); }, 60000);
});
