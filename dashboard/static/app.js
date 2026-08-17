// ── Toast notifications ────────────────────────────────────────────────────
function showToast(msg, type = 'info', duration = 4000) {
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg;
  t.className = 'toast ' + type;
  setTimeout(() => { t.className = 'toast hidden'; }, duration);
}

// ── Sidebar actions ────────────────────────────────────────────────────────
function triggerFetch() {
  const btn = document.getElementById('btn-fetch');
  if (btn) { btn.textContent = '⏳ Fetching…'; btn.disabled = true; }
  showToast('🔄 Job fetch started in background…', 'info', 5000);

  fetch('/run/fetch', { method: 'POST' })
    .then(r => r.json())
    .then(() => {
      showToast('✅ Fetch started! Refresh in a minute to see new jobs.', 'success');
    })
    .catch(() => {
      showToast('❌ Could not start fetch. Is the server running?', 'error');
    })
    .finally(() => {
      if (btn) { btn.textContent = '🔄 Fetch Jobs'; btn.disabled = false; }
    });
}

function triggerScore() {
  const btn = document.getElementById('btn-score');
  if (btn) { btn.textContent = '⏳ Scoring…'; btn.disabled = true; }
  showToast('🧠 AI scoring started… (requires Ollama)', 'info', 6000);

  fetch('/run/score', { method: 'POST' })
    .then(r => r.json())
    .then(() => {
      showToast('✅ AI scoring running in background. Refresh soon to see scores.', 'success');
    })
    .catch(() => {
      showToast('❌ Could not start AI scoring.', 'error');
    })
    .finally(() => {
      if (btn) { btn.textContent = '🧠 Run AI Score'; btn.disabled = false; }
    });
}

// ── Auto-apply from jobs page (shared helper) ──────────────────────────────
function applyJob(jobId, dryRun, headful = false) {
  const btn = document.getElementById('apply-btn-' + jobId);
  if (btn) { btn.textContent = '⏳ Applying…'; btn.disabled = true; }

  fetch('/api/apply/' + jobId, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dry_run: dryRun, headful: headful }),
  })
    .then(r => r.json())
    .then(d => {
      if (btn) { btn.disabled = false; btn.textContent = '🚀 Auto-Apply (dry run)'; }
      if (d.error) {
        showToast('❌ ' + d.error, 'error');
      } else {
        showToast('✅ ' + d.status, 'success');
        setTimeout(() => location.reload(), 2000);
      }
    })
    .catch(err => {
      if (btn) { btn.disabled = false; }
      showToast('❌ Network error: ' + err, 'error');
    });
}
