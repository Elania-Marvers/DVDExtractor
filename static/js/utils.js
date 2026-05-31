export function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function formatBytes(raw) {
  const value = Number(raw);
  if (!Number.isFinite(value) || value < 1024) {
    return `${raw || 0} B`;
  }
  const kb = value / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  const mb = kb / 1024;
  if (mb < 1024) return `${mb.toFixed(2)} MB`;
  return `${(mb / 1024).toFixed(2)} GB`;
}

export function progressMarkup(percent) {
  const numeric = Number(percent);
  const safe = Number.isFinite(numeric) ? Math.max(0, Math.min(100, numeric)) : 0;
  return `<div class="progressWrap"><div class="progress"><span style="width:${safe.toFixed(1)}%"></span></div><span class="small">${safe ? `${safe.toFixed(1)}%` : "0.0%"}</span></div>`;
}

export function rowEmpty(target, cols, message) {
  target.innerHTML = `<tr><td colspan="${cols}" class="empty">${escapeHtml(message)}</td></tr>`;
}

export function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

export function formatStatus(status) {
  return status ? String(status) : "unknown";
}
