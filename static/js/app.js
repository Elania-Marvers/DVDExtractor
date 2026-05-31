const POLL_INTERVAL_MS = 2000;
const HEARTBEAT_MS = 1000;
const MAX_LOG_LINES = 240;
const MAX_ERROR_LINES = 240;

const state = {
  mode: localStorage.getItem('dvd_mode') || 'normal',
};

const debugLines = [];
const errorLines = [];

const statusClass = {
  completed: 'ok',
  failed: 'fail',
  running: 'run',
  starting: 'wait',
  queued: 'wait',
  cancelling: 'canc',
  cancelled: 'canc',
};

const statusLabel = {
  completed: 'Terminé',
  failed: 'Échec',
  running: 'En cours',
  starting: 'Démarrage',
  queued: 'Attente',
  cancelling: 'Annulation',
  cancelled: 'Annulé',
};

function esc(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function bytes(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n < 1024) {
    return `${value || 0} B`;
  }
  const kb = n / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  const mb = kb / 1024;
  if (mb < 1024) return `${mb.toFixed(2)} MB`;
  return `${(mb / 1024).toFixed(2)} GB`;
}

function progressCell(percent) {
  const value = Number(percent);
  const safe = Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0;
  return `<div class="progressWrap"><div class="progress"><span style="width:${safe.toFixed(1)}%"></span></div><span class="small">${value ? `${safe.toFixed(1)}%` : '0.0%'}</span></div>`;
}

function pushLine(targetId, message, list, maxLines) {
  if (!message) return;
  const line = `[${new Date().toLocaleTimeString()}] ${message}`;
  list.push(line);
  if (list.length > maxLines) list.shift();
  const panel = document.getElementById(targetId);
  if (panel) {
    panel.textContent = list.join('\n');
    panel.scrollTop = panel.scrollHeight;
  }
}

function notify(message, error = false) {
  const toaster = document.getElementById('toasts');
  const item = document.createElement('div');
  item.className = `toast ${error ? 'err' : 'ok'}`;
  item.textContent = message;
  toaster.prepend(item);
  setTimeout(() => item.remove(), error ? 5000 : 3000);
}

function rowEmpty(target, cols, message) {
  target.innerHTML = `<tr><td colspan="${cols}" class="empty">${esc(message)}</td></tr>`;
}

function renderStat(id, text) {
  const target = document.getElementById(id);
  if (target) target.textContent = text;
}

function updateCounts(data) {
  renderStat('drives-count', `${data.drives || 0} trouvé(s)`);
  renderStat('jobs-count', `${data.jobs || 0} actif(s)`);
  renderStat('files-count', `${data.files || 0} fichier(s)`);
  renderStat('stat-drives', `Lecteurs : ${data.drives || 0}`);
  renderStat('stat-jobs', `Jobs : ${data.jobs || 0}`);
  renderStat('stat-files', `MP4 : ${data.files || 0}`);
}

function renderDrives(items) {
  const tbody = document.getElementById('drives-body');
  tbody.innerHTML = '';
  updateCounts({ drives: (items || []).length });

  if (!items || !items.length) {
    rowEmpty(tbody, 5, 'Aucun lecteur détecté');
    return;
  }

  for (const item of items) {
    const status = (item.state || 'unknown').toLowerCase();
    const pill = status === 'inserted' ? 'ok' : status === 'empty' ? 'canc' : 'wait';
    const encryption = item.encryption || {};

    const encryptedText =
      encryption.encrypted === null || typeof encryption.encrypted === 'undefined'
        ? `Méthode: ${esc(encryption.method || 'inconnue')}`
        : `${encryption.encrypted ? 'Oui' : 'Non'} (${esc(encryption.method || 'n/a')})`;

    const row = document.createElement('tr');
    row.innerHTML = `
      <td>
        <div class="title-line">${esc(item.name || 'Lecteur inconnu')}</div>
        <div class="mono muted">${esc(item.device || '-')}</div>
      </td>
      <td>${esc(item.drive_type || '-')}</td>
      <td><span class="pill ${pill}">${esc(item.state || 'unknown')}</span></td>
      <td>${encryptedText}</td>
      <td>
        <button class="btn" ${item.inserted ? '' : 'disabled'} data-device="${esc(item.device || '')}" data-title="${esc(item.name || 'dvd')}">
          Copier en MP4
        </button>
      </td>
    `;

    const button = row.querySelector('button');
    if (button) {
      button.addEventListener('click', () => startRip(button.dataset.device, button.dataset.title));
    }

    tbody.appendChild(row);
  }
}

function renderJobs(items) {
  const tbody = document.getElementById('jobs-body');
  tbody.innerHTML = '';
  updateCounts({ jobs: (items || []).length });

  if (!items || !items.length) {
    rowEmpty(tbody, 6, 'Aucun job actif');
    return;
  }

  for (const job of items) {
    const status = job.status || 'queued';
    const statusText = statusLabel[status] || status;
    const statusClassName = statusClass[status] || 'wait';
    const shortId = (job.id || '').slice(0, 8);
    const attempt = (job.attempts && job.attempts_total) ? `${job.attempts}/${job.attempts_total}` : '-';
    const errorText = (job.error || '-').trim();
    const logs = (job.log_tail || '').trim();
    const canCancel = ['queued', 'starting', 'running'].includes(status);

    const row = document.createElement('tr');
    row.innerHTML = `
      <td class="mono muted" title="${esc(job.id || '')}">${esc(shortId || '-')}</td>
      <td>
        <div><strong>${esc(job.device || '-')}</strong></div>
        <div class="mono muted">${esc(job.storage_path || '-')}</div>
        <div class="small">Mode ${esc(job.mode || 'normal')} · Tentative ${esc(attempt)} · ${esc(job.current_command || 'n/a')}</div>
      </td>
      <td><span class="pill ${statusClassName}">${statusText}</span></td>
      <td>${progressCell(job.progress)}</td>
      <td>
        <div class="error-line">${esc(errorText || 'Aucune erreur')}</div>
        <pre class="pre-wrap">${esc(logs || 'Aucun log disponible')}</pre>
      </td>
      <td>
        ${canCancel ? `<button class="btn btn-danger" data-job="${esc(job.id || '')}">Annuler</button>` : '-'}
      </td>
    `;

    const btn = row.querySelector('[data-job]');
    if (btn) {
      btn.addEventListener('click', () => cancelJob(btn.dataset.job || ''));
    }
    tbody.appendChild(row);
  }
}

function renderFiles(items) {
  const target = document.getElementById('files');
  updateCounts({ files: (items || []).length });

  if (!items || !items.length) {
    target.innerHTML = '<div class="empty">Aucun fichier généré pour le moment.</div>';
    return;
  }

  const rows = (items || [])
    .map((file) => `<tr><td>${esc(file.name)}</td><td>${bytes(file.size)}</td><td>${esc(new Date(file.modified).toLocaleString())}</td><td><a href="${esc(file.url)}" download>${esc(file.name)}</a></td></tr>`)
    .join('');

  target.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead><tr><th>Nom</th><th>Taille</th><th>Modifié</th><th>Lien</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

async function fetchJSON(url, options = {}) {
  const response = await fetch(url, options);
  const body = await response.text();
  if (!response.ok) {
    let error = body || `HTTP ${response.status}`;
    try {
      const parsed = JSON.parse(body);
      if (parsed && parsed.error) error = parsed.error;
    } catch (_err) {}
    throw new Error(error);
  }
  return body ? JSON.parse(body) : {};
}

async function updateMode(mode) {
  state.mode = mode;
  localStorage.setItem('dvd_mode', mode);
  const modeBadge = document.getElementById('mode-tag');
  const modeHint = document.getElementById('mode-help');
  const normalBtn = document.getElementById('mode-normal');
  const engineerBtn = document.getElementById('mode-engineer');

  modeBadge.textContent = `Mode: ${mode === 'engineer' ? 'ingénieur' : 'normal'}`;
  modeHint.textContent =
    mode === 'engineer'
      ? 'Mode ingénieur: stratégies supplémentaires + dump natif C++ + retries ciblés.'
      : 'Mode normal: pipeline ffmpeg standard.';

  normalBtn.classList.toggle('active', mode !== 'engineer');
  engineerBtn.classList.toggle('active', mode === 'engineer');
}

async function loadInfo() {
  try {
    const info = await fetchJSON('/api/info');
    document.getElementById('storage').textContent = info.storage_path || 'indisponible';
    document.getElementById('poll').textContent = String(info.poll_interval || 2);
  } catch (err) {
    document.getElementById('storage').textContent = 'indisponible';
  }
}

async function loadDrives() {
  const data = await fetchJSON('/api/drives');
  renderDrives(data.drives || []);
}

async function loadJobs() {
  const data = await fetchJSON('/api/jobs');
  renderJobs(data.jobs || []);
}

async function loadFiles() {
  const data = await fetchJSON('/api/files');
  renderFiles(data.files || []);
}

async function loadHeartbeat() {
  try {
    const data = await fetchJSON('/api/heartbeat');
    const hbDot = document.getElementById('hb-dot');
    const hbState = document.getElementById('hb-state');
    const hbDetails = document.getElementById('hb-details');

    const ageMs = Math.max(0, Math.round((Date.now() / 1000 - Number(data.now || 0)) * 1000));
    if (ageMs <= 2500) {
      hbDot.classList.remove('off', 'fail');
      hbState.textContent = `heartbeat ok · ${ageMs} ms`;
    } else {
      hbDot.classList.add('off');
      hbState.textContent = `heartbeat lent · ${ageMs} ms`;
    }

    if ((data.active_jobs || []).length) {
      const lines = [`jobs: ${data.jobs_total || 0} · actifs: ${data.active_jobs.length}`];
      for (const item of data.active_jobs) {
        const short = (item.id || '').slice(0, 8);
        lines.push(`${short} ${item.status} ${item.device || '-'} ${item.current_command ? `· ${item.current_command}` : ''} ${item.error || ''}`);
        if (item.error) pushLine('error-stream', `Job ${short}: ${item.error}`, errorLines, MAX_ERROR_LINES);
      }
      pushLine('debug-stream', lines.join('\n'), debugLines, MAX_LOG_LINES);
    }

    hbDetails.textContent = `jobs actifs: ${data.active_jobs?.length || 0} · heartbeat: ${new Date((data.now || 0) * 1000).toLocaleTimeString()}`;
  } catch (err) {
    const hbDot = document.getElementById('hb-dot');
    const hbState = document.getElementById('hb-state');
    const hbDetails = document.getElementById('hb-details');
    hbDot.classList.add('fail');
    hbState.textContent = `heartbeat perdu: ${err.message || 'erreur'}`;
    hbDetails.textContent = hbState.textContent;
  }
}

async function startRip(device, title) {
  if (!device) return;
  try {
    await fetchJSON('/api/rip', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ device, title, mode: state.mode }),
    });
    notify(`Extraction lancée en mode ${state.mode}: ${title}`);
  } catch (err) {
    notify(err.message || 'Impossible de lancer', true);
  }

  await refreshData();
}

async function cancelJob(jobId) {
  if (!jobId) return;
  try {
    const response = await fetch(`/api/rip/${encodeURIComponent(jobId)}`, { method: 'DELETE' });
    const raw = await response.text();
    if (!response.ok) {
      let msg = raw;
      try {
        const payload = JSON.parse(raw);
        if (payload && payload.error) msg = payload.error;
      } catch (_err) {}
      throw new Error(msg || `HTTP ${response.status}`);
    }
    notify(`Annulation demandée pour ${jobId}`);
  } catch (err) {
    notify(err.message || 'Impossible d\'annuler', true);
  }
  await refreshData();
}

async function refreshData() {
  try {
    await Promise.all([loadInfo(), loadDrives(), loadJobs(), loadFiles()]);
  } catch (err) {
    notify(err.message || 'Erreur de rafraîchissement', true);
    pushLine('error-stream', err.message || 'Erreur de rafraîchissement', errorLines, MAX_ERROR_LINES);
  }
}

function initModeControls() {
  const normalBtn = document.getElementById('mode-normal');
  const engineerBtn = document.getElementById('mode-engineer');
  normalBtn.addEventListener('click', () => updateMode('normal'));
  engineerBtn.addEventListener('click', () => updateMode('engineer'));
}

initModeControls();
updateMode(state.mode);
refreshData();
loadHeartbeat();
setInterval(refreshData, POLL_INTERVAL_MS);
setInterval(loadHeartbeat, HEARTBEAT_MS);
