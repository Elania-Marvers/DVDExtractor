import { HEARTBEAT_NODE_IDS, MAX_STREAM_LINES, STATUS_CLASS, STATUS_LABEL } from "./config.js";
import { escapeHtml, formatBytes, progressMarkup, rowEmpty, formatStatus } from "./utils.js";

export class DashboardRenderer {
  constructor(api) {
    this.api = api;
    this.debugLines = [];
    this.errorLines = [];
    this.counts = { drives: 0, jobs: 0, files: 0 };
  }

  updateStat(id, value) {
    const target = document.getElementById(id);
    if (target) target.textContent = value;
  }

  setHeartbeat(payload) {
    const hbDot = document.getElementById(HEARTBEAT_NODE_IDS.dot);
    const hbState = document.getElementById(HEARTBEAT_NODE_IDS.state);
    const hbDetails = document.getElementById(HEARTBEAT_NODE_IDS.details);
    if (!hbDot || !hbState || !hbDetails) return;

    const now = Number(payload.now || 0);
    const jobs = payload.active_jobs || [];
    const ageMs = Math.max(0, Math.round((Date.now() / 1000 - now) * 1000));
    if (ageMs <= 2500) {
      hbDot.classList.remove("off", "fail");
      hbState.textContent = `heartbeat ok · ${ageMs} ms`;
    } else {
      hbDot.classList.add("off");
      hbState.textContent = `heartbeat lent · ${ageMs} ms`;
    }

    if (jobs.length) {
      const lines = [`jobs: ${payload.jobs_total || 0} · actifs: ${payload.active_jobs.length}`];
      for (const item of jobs) {
        const short = (item.id || "").slice(0, 8);
        lines.push(
          `${short} ${item.status} ${item.device || "-"} ${item.current_command ? `· ${item.current_command}` : ""} ${item.error || ""}`,
        );
        if (item.error) {
          this.pushLine("error-stream", `Job ${short}: ${item.error}`, this.errorLines, MAX_STREAM_LINES);
        }
      }
      this.pushLine("debug-stream", lines.join("\n"), this.debugLines, MAX_STREAM_LINES);
    }

    hbDetails.textContent = `jobs actifs: ${payload.active_jobs?.length || 0} · heartbeat: ${new Date(
      (payload.now || 0) * 1000,
    ).toLocaleTimeString()}`;
  }

  pushLine(targetId, message, list, maxLines) {
    if (!message) return;
    const line = `[${new Date().toLocaleTimeString()}] ${message}`;
    list.push(line);
    if (list.length > maxLines) list.shift();
    const panel = document.getElementById(targetId);
    if (panel) {
      panel.textContent = list.join("\n");
      panel.scrollTop = panel.scrollHeight;
    }
  }

  notify(message, isError = false) {
    const toaster = document.getElementById("toasts");
    if (!toaster) return;

    const item = document.createElement("div");
    item.className = `toast ${isError ? "err" : "ok"}`;
    item.textContent = message;
    toaster.prepend(item);
    setTimeout(() => item.remove(), isError ? 5000 : 3000);
  }

  renderCounts(data) {
    this.counts = { ...this.counts, ...data };
    this.updateStat("drives-count", `${this.counts.drives || 0} trouvé(s)`);
    this.updateStat("jobs-count", `${this.counts.jobs || 0} job(s)`);
    this.updateStat("files-count", `${this.counts.files || 0} fichier(s)`);
    this.updateStat("stat-drives", `Lecteurs : ${this.counts.drives || 0}`);
    this.updateStat("stat-jobs", `Jobs : ${this.counts.jobs || 0}`);
    this.updateStat("stat-files", `MP4 : ${this.counts.files || 0}`);
  }

  renderMode(mode, saveMode) {
    const tag = document.getElementById("mode-tag");
    const hint = document.getElementById("mode-help");
    const normalBtn = document.getElementById("mode-normal");
    const engineerBtn = document.getElementById("mode-engineer");

    if (tag) tag.textContent = `Mode: ${mode === "engineer" ? "ingénieur" : "normal"}`;
    if (hint) {
      hint.textContent =
        mode === "engineer"
          ? "Mode ingénieur: stratégies supplémentaires + native + retries ciblés."
          : "Mode normal: pipeline ffmpeg standard.";
    }
    if (normalBtn) normalBtn.classList.toggle("active", mode !== "engineer");
    if (engineerBtn) engineerBtn.classList.toggle("active", mode === "engineer");

    saveMode(mode);
  }

  renderDrives(items) {
    const tbody = document.getElementById("drives-body");
    if (!tbody) return;
    tbody.innerHTML = "";
    this.renderCounts({ drives: (items || []).length });

    if (!items || !items.length) {
      rowEmpty(tbody, 5, "Aucun lecteur détecté");
      return;
    }

    for (const item of items) {
      const status = (item.state || "unknown").toLowerCase();
      const pill = status === "inserted" ? "ok" : status === "empty" ? "canc" : "wait";
      const encryption = item.encryption || {};
      const encryptedText =
        encryption.encrypted === null || typeof encryption.encrypted === "undefined"
          ? `Méthode: ${escapeHtml(encryption.method || "inconnue")}`
          : `${encryption.encrypted ? "Oui" : "Non"} (${escapeHtml(encryption.method || "n/a")})`;

      const row = document.createElement("tr");
      row.innerHTML = `
        <td>
          <div class="title-line">${escapeHtml(item.name || "Lecteur inconnu")}</div>
          <div class="mono muted">${escapeHtml(item.device || "-")}</div>
        </td>
        <td>${escapeHtml(item.drive_type || "-")}</td>
        <td><span class="pill ${pill}">${escapeHtml(item.state || "unknown")}</span></td>
        <td>${encryptedText}</td>
        <td>
          <button class="btn" ${item.inserted ? "" : "disabled"} data-device="${escapeHtml(item.device || "")}" data-title="${escapeHtml(item.name || "dvd")}">
            Copier en MP4
          </button>
        </td>`;

      const button = row.querySelector("button");
      if (button) {
        button.addEventListener("click", () => this.onStartRip?.(button.dataset.device, button.dataset.title));
      }
      tbody.appendChild(row);
    }
  }

  renderJobs(items) {
    const tbody = document.getElementById("jobs-body");
    if (!tbody) return;
    tbody.innerHTML = "";
    this.renderCounts({ jobs: (items || []).length });

    if (!items || !items.length) {
      rowEmpty(tbody, 6, "Aucun job actif");
      return;
    }

    for (const job of items) {
      const status = job.status || "queued";
      const statusText = STATUS_LABEL[status] || formatStatus(status);
      const statusClass = STATUS_CLASS[status] || "wait";
      const shortId = (job.id || "").slice(0, 8);
      const attempts = job.attempts && job.attempts_total ? `${job.attempts}/${job.attempts_total}` : "-";
      const errorText = (job.error || "-").trim();
      const logs = (job.log_tail || "").trim();
      const canCancel = ["queued", "starting", "running"].includes(status);

      const row = document.createElement("tr");
      row.innerHTML = `
        <td class="mono muted" title="${escapeHtml(job.id || "")}">${escapeHtml(shortId || "-")}</td>
        <td>
          <div><strong>${escapeHtml(job.device || "-")}</strong></div>
          <div class="mono muted">${escapeHtml(job.storage_path || "-")}</div>
          <div class="small">Mode ${escapeHtml(job.mode || "normal")} · Tentative ${escapeHtml(attempts)} · ${escapeHtml(job.current_command || "n/a")}</div>
        </td>
        <td><span class="pill ${statusClass}">${statusText}</span></td>
        <td>${progressMarkup(job.progress)}</td>
        <td>
          <div class="error-line">${escapeHtml(errorText || "Aucune erreur")}</div>
          <pre class="pre-wrap">${escapeHtml(logs || "Aucun log disponible")}</pre>
        </td>
        <td>
          ${canCancel ? `<button class="btn btn-danger" data-job="${escapeHtml(job.id || "")}">Annuler</button>` : "-"}
        </td>`;

      const btn = row.querySelector("[data-job]");
      if (btn) {
        btn.addEventListener("click", () => this.onCancelJob?.(btn.dataset.job || ""));
      }
      tbody.appendChild(row);
    }
  }

  renderFiles(items) {
    const target = document.getElementById("files");
    if (!target) return;
    this.renderCounts({ files: (items || []).length });

    if (!items || !items.length) {
      target.innerHTML = '<div class="empty">Aucun fichier généré pour le moment.</div>';
      return;
    }

    const rows = items
      .map(
        (file) =>
          `<tr><td>${escapeHtml(file.name)}</td><td>${formatBytes(file.size)}</td><td>${escapeHtml(new Date(file.modified).toLocaleString())}</td><td><a href="${escapeHtml(file.url)}" download>${escapeHtml(file.name)}</a></td></tr>`,
      )
      .join("");

    target.innerHTML = `
      <div class="table-wrap">
        <table>
          <thead><tr><th>Nom</th><th>Taille</th><th>Modifié</th><th>Lien</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }
}
