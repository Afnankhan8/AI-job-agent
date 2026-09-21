/* ══════════════════════════════════════════════════════════════════════════════
   AI JOB AGENT DASHBOARD — 3D INTERACTION ENGINE + LIVE DATA LAYER
   All original functionality preserved. Added premium 3D interaction layer.
══════════════════════════════════════════════════════════════════════════════ */

'use strict';

/* ══════════════════════════════════════════════════════════════════════════════
   SECTION 1 — PREMIUM 3D INTERACTION ENGINE
══════════════════════════════════════════════════════════════════════════════ */

// ── Spring physics engine ──────────────────────────────────────────────────────
class SpringValue {
  constructor(initial = 0, stiffness = 200, damping = 22, mass = 1) {
    this.value    = initial;
    this.target   = initial;
    this.velocity = 0;
    this.stiffness = stiffness;
    this.damping   = damping;
    this.mass      = mass;
  }
  step(dt) {
    const force   = -this.stiffness * (this.value - this.target);
    const damp    = -this.damping * this.velocity;
    const accel   = (force + damp) / this.mass;
    this.velocity += accel * dt;
    this.value    += this.velocity * dt;
  }
  setTarget(t) { this.target = t; }
  isAtRest(threshold = 0.001) {
    return Math.abs(this.value - this.target) < threshold && Math.abs(this.velocity) < threshold;
  }
}

// ── Global cursor state ───────────────────────────────────────────────────────
const cursor = {
  x: window.innerWidth  / 2,
  y: window.innerHeight / 2,
  rawX: window.innerWidth  / 2,
  rawY: window.innerHeight / 2,
  springX: new SpringValue(window.innerWidth / 2, 80, 14),
  springY: new SpringValue(window.innerHeight / 2, 80, 14),
};

document.addEventListener('mousemove', (e) => {
  cursor.rawX = e.clientX;
  cursor.rawY = e.clientY;
  cursor.springX.setTarget(e.clientX);
  cursor.springY.setTarget(e.clientY);
});

// ── Cursor glow element ───────────────────────────────────────────────────────
let glowEl = null;
function initCursorGlow() {
  glowEl = document.createElement('div');
  glowEl.id = 'cursor-glow';
  document.body.appendChild(glowEl);
}

// ── 3D tilt for cards ─────────────────────────────────────────────────────────
const tiltCards = new Map();

function registerTiltCard(el, options = {}) {
  const {
    maxTilt   = 10,
    scale     = 1.03,
    glare     = true,
    spring    = { stiffness: 180, damping: 20 },
    depth     = 8,
  } = options;

  const rotX = new SpringValue(0, spring.stiffness, spring.damping);
  const rotY = new SpringValue(0, spring.stiffness, spring.damping);
  const scaleV = new SpringValue(1, spring.stiffness * 0.8, spring.damping * 1.2);

  // Build glare element
  let glareEl = null;
  if (glare) {
    glareEl = document.createElement('div');
    glareEl.style.cssText = `
      position:absolute; inset:0; border-radius:inherit;
      pointer-events:none; z-index:2; overflow:hidden;
    `;
    const glareInner = document.createElement('div');
    glareInner.style.cssText = `
      position:absolute; width:200%; height:200%;
      background:linear-gradient(135deg,rgba(255,255,255,0.12) 0%,transparent 50%);
      transition:none; will-change:transform;
    `;
    glareEl.appendChild(glareInner);
    el.style.position = 'relative';
    el.style.overflow = 'hidden';
    el.appendChild(glareEl);
  }

  const state = { hovering: false, glareInner };
  tiltCards.set(el, { rotX, rotY, scaleV, state, maxTilt, scale, depth, glareEl });

  el.addEventListener('mouseenter', () => {
    state.hovering = true;
    scaleV.setTarget(scale);
  });
  el.addEventListener('mouseleave', () => {
    state.hovering = false;
    rotX.setTarget(0);
    rotY.setTarget(0);
    scaleV.setTarget(1);
    if (glareEl) {
      const gi = glareEl.querySelector('div');
      if (gi) gi.style.transform = 'translate(-50%, -50%)';
    }
  });
  el.addEventListener('mousemove', (e) => {
    if (!state.hovering) return;
    const rect   = el.getBoundingClientRect();
    const cx     = rect.left + rect.width  / 2;
    const cy     = rect.top  + rect.height / 2;
    const dx     = (e.clientX - cx) / (rect.width  / 2);
    const dy     = (e.clientY - cy) / (rect.height / 2);
    rotY.setTarget(dx * maxTilt);
    rotX.setTarget(-dy * maxTilt);

    if (glareEl) {
      const gi = glareEl.querySelector('div');
      if (gi) {
        const gx = (dx + 1) / 2 * 100;
        const gy = (dy + 1) / 2 * 100;
        gi.style.transform = `translate(${gx - 50}%, ${gy - 50}%)`;
      }
    }
  });
}

// ── Parallax layers ───────────────────────────────────────────────────────────
const parallaxLayers = [];
function registerParallax(el, depth = 0.02) {
  parallaxLayers.push({ el, depth });
}

// ── Main animation loop ───────────────────────────────────────────────────────
let lastTime = 0;
function mainAnimationLoop(ts) {
  const dt = Math.min((ts - lastTime) / 1000, 0.05);
  lastTime = ts;

  // Step cursor springs
  cursor.springX.step(dt);
  cursor.springY.step(dt);
  cursor.x = cursor.springX.value;
  cursor.y = cursor.springY.value;

  // Move cursor glow
  if (glowEl) {
    glowEl.style.left = cursor.x + 'px';
    glowEl.style.top  = cursor.y + 'px';
  }

  // Update tilt cards
  tiltCards.forEach(({ rotX, rotY, scaleV, state, maxTilt, scale, depth }, el) => {
    rotX.step(dt);
    rotY.step(dt);
    scaleV.step(dt);
    if (!rotX.isAtRest(0.005) || !rotY.isAtRest(0.005) || !scaleV.isAtRest(0.0005)) {
      el.style.transform = `
        perspective(var(--perspective))
        rotateX(${rotX.value}deg)
        rotateY(${rotY.value}deg)
        scale(${scaleV.value})
        translateZ(${state.hovering ? depth : 0}px)
      `;
    }
  });

  // Parallax
  const cx = window.innerWidth  / 2;
  const cy = window.innerHeight / 2;
  parallaxLayers.forEach(({ el, depth }) => {
    const dx = (cursor.x - cx) * depth;
    const dy = (cursor.y - cy) * depth;
    el.style.transform = `translate(${dx}px, ${dy}px)`;
  });

  requestAnimationFrame(mainAnimationLoop);
}

// ── Floating particles ────────────────────────────────────────────────────────
function spawnParticles() {
  const colors = [
    'rgba(157,113,255,0.5)',
    'rgba(34,211,238,0.4)',
    'rgba(244,114,182,0.35)',
    'rgba(52,211,153,0.35)',
    'rgba(255,255,255,0.2)',
  ];

  function createParticle() {
    const el = document.createElement('div');
    el.className = 'particle';
    const size = Math.random() * 3 + 1;
    const color = colors[Math.floor(Math.random() * colors.length)];
    const duration = Math.random() * 20 + 15;
    const delay = Math.random() * 15;
    const left = Math.random() * 100;
    el.style.cssText = `
      width:${size}px; height:${size}px;
      background:${color};
      left:${left}%;
      animation-duration:${duration}s;
      animation-delay:${delay}s;
      box-shadow:0 0 ${size * 2}px ${color};
    `;
    document.body.appendChild(el);
    // Remove after several cycles to prevent DOM bloat
    setTimeout(() => el.remove(), (duration + delay + 5) * 1000 * 3);
  }

  // Initial batch
  for (let i = 0; i < 18; i++) createParticle();
  // Ongoing spawn
  setInterval(() => {
    if (document.querySelectorAll('.particle').length < 30) createParticle();
  }, 3000);
}

// ── Section reveal animations (Intersection Observer) ─────────────────────────
function initReveal() {
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.style.opacity    = '1';
          entry.target.style.transform  = 'translateY(0) scale(1)';
          observer.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.08, rootMargin: '0px 0px -40px 0px' }
  );

  // Target cards and grid items
  ['.stat-card', '.action-card', '.job-card', '.feed-card', '.side-panel', '.app-badge'].forEach(sel => {
    document.querySelectorAll(sel).forEach((el, i) => {
      el.style.cssText += `
        opacity:0;
        transform:translateY(24px) scale(0.97);
        transition:opacity 0.5s cubic-bezier(0.34,1.2,0.64,1) ${i * 60}ms,
                   transform 0.5s cubic-bezier(0.34,1.2,0.64,1) ${i * 60}ms;
      `;
      observer.observe(el);
    });
  });
}

// ── Button ripple effect ──────────────────────────────────────────────────────
function addRipple(e) {
  const btn  = e.currentTarget;
  const rect = btn.getBoundingClientRect();
  const x    = e.clientX - rect.left;
  const y    = e.clientY - rect.top;

  const ripple = document.createElement('span');
  ripple.style.cssText = `
    position:absolute; border-radius:50%; pointer-events:none;
    background:rgba(255,255,255,0.18);
    width:0; height:0;
    left:${x}px; top:${y}px;
    transform:translate(-50%,-50%);
    animation:rippleAnim 0.55s cubic-bezier(0.4,0,0.2,1) forwards;
  `;
  btn.style.position = 'relative';
  btn.style.overflow = 'hidden';
  btn.appendChild(ripple);
  ripple.addEventListener('animationend', () => ripple.remove());
}

// Inject ripple keyframe
const rippleStyle = document.createElement('style');
rippleStyle.textContent = `
  @keyframes rippleAnim {
    from { width:0; height:0; opacity:0.8; }
    to   { width:300px; height:300px; opacity:0; }
  }
`;
document.head.appendChild(rippleStyle);

// ── Initialize all 3D effects ─────────────────────────────────────────────────
function init3D() {
  // Cursor glow
  initCursorGlow();

  // Particles
  spawnParticles();

  // Tilt — stat cards (gentler)
  document.querySelectorAll('.stat-card').forEach(el =>
    registerTiltCard(el, { maxTilt: 7, scale: 1.04, depth: 6 })
  );

  // Tilt — action cards (more prominent)
  document.querySelectorAll('.action-card').forEach(el =>
    registerTiltCard(el, { maxTilt: 12, scale: 1.05, depth: 10 })
  );

  // Tilt — feed cards (subtle)
  document.querySelectorAll('.feed-card').forEach(el =>
    registerTiltCard(el, { maxTilt: 4, scale: 1.02, depth: 4, glare: false })
  );

  // Tilt — side panels
  document.querySelectorAll('.side-panel').forEach(el =>
    registerTiltCard(el, { maxTilt: 5, scale: 1.015, depth: 4, glare: false })
  );

  // Tilt — job cards
  document.querySelectorAll('.job-card').forEach(el =>
    registerTiltCard(el, { maxTilt: 4, scale: 1.02, depth: 4, glare: false })
  );

  // Tilt — app badges
  document.querySelectorAll('.app-badge').forEach(el =>
    registerTiltCard(el, { maxTilt: 8, scale: 1.04, depth: 6 })
  );

  // Ripple — all buttons
  document.querySelectorAll('button, .btn-filter, .btn-apply').forEach(el =>
    el.addEventListener('mousedown', addRipple)
  );

  // Reveal animations
  initReveal();

  // Nav link hover depth
  document.querySelectorAll('.nav-link').forEach(el => {
    el.addEventListener('mouseenter', () => {
      el.style.transition = 'all 0.25s cubic-bezier(0.34,1.2,0.64,1)';
    });
  });

  // Sidebar brand logo parallax
  const brandLogo = document.querySelector('.brand-logo');
  if (brandLogo) registerParallax(brandLogo, 0.015);

  // Start main loop
  requestAnimationFrame(mainAnimationLoop);
}

// Re-register tilt after dynamic content loads
function reinit3DForNewContent() {
  document.querySelectorAll('.feed-card:not([data-tilt-init])').forEach(el => {
    el.dataset.tiltInit = '1';
    registerTiltCard(el, { maxTilt: 4, scale: 1.02, depth: 4, glare: false });
    el.addEventListener('mousedown', addRipple);
  });
  document.querySelectorAll('.job-card:not([data-tilt-init])').forEach(el => {
    el.dataset.tiltInit = '1';
    registerTiltCard(el, { maxTilt: 4, scale: 1.02, depth: 4, glare: false });
  });
}

/* ══════════════════════════════════════════════════════════════════════════════
   SECTION 2 — ORIGINAL LIVE DATA ENGINE (UNCHANGED FUNCTIONALITY)
══════════════════════════════════════════════════════════════════════════════ */

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
    const ease = 1 - Math.pow(1 - progress, 3);
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
      if (compList && d.top_companies) {
        compList.innerHTML = d.top_companies.map(([name, count], i) => `
          <div class="trend-row">
            <span class="trend-rank">${i + 1}</span>
            <span class="trend-name" title="${name}">${name}</span>
            <span class="trend-count">${count}</span>
          </div>
        `).join('');
      }

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
  inner.innerHTML = items + items;
}

function buildFeedCard(job, rank) {
  const fCls    = freshnessClass(job.freshness);
  const rankCls = rank <= 3 ? `rank-${rank}` : '';
  const rec     = (job.recommendation || '').toLowerCase();
  const recCls  = rec || 'unscored';
  const scoreStr = job.score !== null && job.score !== undefined ? `${job.score}` : '?';
  const dashArr  = job.score !== null && job.score !== undefined ? `${job.score}, 100` : `0, 100`;
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

      buildTicker(jobs);

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

        const html = jobs.slice(0, 20).map((j, i) => buildFeedCard(j, i + 1)).join('');
        feedEl.innerHTML = html;

        // Re-register 3D for newly added cards
        setTimeout(reinit3DForNewContent, 50);
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
    .then(() => {
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
      const modal   = document.getElementById('inspect-modal');
      const content = document.getElementById('inspect-modal-content');
      if (!modal || !content) return;

      const logsHtml = (d.logs_parsed || []).map(l =>
        `<div class="log-line"><code>${l}</code></div>`
      ).join('');

      const imgHtml = d.screenshot_url
        ? `<div style="margin-top:1rem">
             <div class="detail-label">Captured Playwright Screenshot</div>
             <a href="${d.screenshot_url}" target="_blank">
               <img src="${d.screenshot_url}" style="width:100%;max-height:350px;object-fit:cover;border-radius:10px;border:1px solid var(--border)">
             </a>
           </div>`
        : `<div style="color:var(--text-3);margin-top:1rem">No screenshot recorded.</div>`;

      content.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1.25rem">
          <div>
            <h2 style="font-family:var(--font-display);font-size:19px;font-weight:800;letter-spacing:-0.02em">${d.title}</h2>
            <p style="font-size:12px;color:var(--text-2);margin-top:3px">${d.company} · Application #${d.id}</p>
          </div>
          <span class="status-badge status-${(d.status||'').toLowerCase()}">${d.status}</span>
        </div>

        ${d.error_reason ? `<div style="padding:0.85rem;background:rgba(251,146,60,0.08);border:1px solid rgba(251,146,60,0.25);border-radius:10px;font-size:12px;color:var(--orange);margin-bottom:1.1rem;line-height:1.6">
          <strong>⚠ Point of Stoppage:</strong> ${d.error_reason}
        </div>` : ''}

        <div style="display:flex;gap:0.5rem;margin-bottom:1.1rem">
          <a href="${d.job_url}" target="_blank" class="btn-filter" style="font-size:12px;padding:5px 12px;text-decoration:none;border-radius:8px">
            Open Job Posting ↗
          </a>
          <button class="btn-clear" style="font-size:12px;padding:5px 12px;border-radius:8px" onclick="triggerLogin()">
            🔑 Run Session Login
          </button>
        </div>

        <div class="detail-label" style="margin-top:1rem">Execution Logs</div>
        <div style="background:var(--bg);padding:0.9rem;border-radius:10px;max-height:200px;overflow-y:auto;font-family:var(--font-mono);font-size:11.5px;color:var(--text-2);border:1px solid var(--border);line-height:1.7">
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
      const icons = { APPLIED: '✅', DRY_RUN: '🧪', REQUIRES_MANUAL: '👆', FAILED: '❌', SKIPPED: '⏭️' };
      const icon  = icons[d.status] || '📋';
      const applicationUrl = /^https?:\/\//i.test(d.application_url || '')
        ? ` — <a href="${d.application_url.replace(/"/g, '&quot;')}" target="_blank" rel="noopener noreferrer">Open application page</a>`
        : '';
      showToast(`${icon} ${d.status}${d.error ? ' — ' + d.error.slice(0, 80) : ''}${applicationUrl}`,
        d.status === 'APPLIED' ? 'success' : 'info', icon);
    })
    .catch(() => {
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

/* ══════════════════════════════════════════════════════════════════════════════
   SECTION 3 — INITIALIZATION
══════════════════════════════════════════════════════════════════════════════ */

document.addEventListener('DOMContentLoaded', () => {
  // Core data layer (original)
  startClock();
  loadStats();
  loadFeed();
  loadTrending();

  // Poll data
  setInterval(() => { loadStats(); loadFeed(); }, 30000);
  setInterval(() => { loadTrending(); }, 60000);

  // 3D interaction layer (new, purely visual)
  // Small delay so initial layout is painted before we measure elements
  setTimeout(() => {
    if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      init3D();
    }
  }, 100);

  // Close modal on overlay click
  const overlay = document.getElementById('inspect-modal');
  if (overlay) {
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) closeInspectModal();
    });
  }

  // Keyboard accessibility
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeInspectModal();
  });
});
