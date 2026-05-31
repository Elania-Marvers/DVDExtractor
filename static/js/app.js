import { ApiClient } from "./api.js";
import { DashboardRenderer } from "./views.js";
import { LOCAL_STORAGE_KEYS, POLL_INTERVAL_MS, HEARTBEAT_MS, MAX_STREAM_LINES } from "./config.js";

class DashboardApp {
  constructor() {
    this.api = new ApiClient();
    this.renderer = new DashboardRenderer(this.api);
    this.state = {
      mode: localStorage.getItem(LOCAL_STORAGE_KEYS.mode) || "normal",
    };

    this.renderer.onStartRip = this.startRip.bind(this);
    this.renderer.onCancelJob = this.cancelRip.bind(this);
  }

  init() {
    this.bindModeButtons();
    this.refreshInfo();
    this.refreshAll();
    this.refreshHeartbeat();

    this.renderer.renderMode(this.state.mode, (mode) => {
      this.state.mode = mode;
    });

    setInterval(() => this.refreshAll(), POLL_INTERVAL_MS);
    setInterval(() => this.refreshHeartbeat(), HEARTBEAT_MS);
  }

  bindModeButtons() {
    const normalBtn = document.getElementById("mode-normal");
    const engineerBtn = document.getElementById("mode-engineer");
    if (!normalBtn || !engineerBtn) return;

    normalBtn.addEventListener("click", () => this.updateMode("normal"));
    engineerBtn.addEventListener("click", () => this.updateMode("engineer"));
  }

  updateMode(mode) {
    this.state.mode = mode;
    localStorage.setItem(LOCAL_STORAGE_KEYS.mode, mode);
    this.renderer.renderMode(mode, () => {});
    this.renderer.notify(`Mode mis à jour: ${mode}`);
  }

  async refreshInfo() {
    try {
      const info = await this.api.info();
      const storageNode = document.getElementById("storage");
      const pollNode = document.getElementById("poll");
      if (storageNode) storageNode.textContent = info.storage_path || "indisponible";
      if (pollNode) pollNode.textContent = String(info.poll_interval || 2);
    } catch (_err) {
      const storageNode = document.getElementById("storage");
      if (storageNode) storageNode.textContent = "indisponible";
    }
  }

  async refreshAll() {
    try {
      const [drivesResp, jobsResp, filesResp] = await Promise.all([
        this.api.drives(),
        this.api.jobs(),
        this.api.files(),
      ]);

      this.renderer.renderDrives(drivesResp.drives || []);
      this.renderer.renderJobs(jobsResp.jobs || []);
      this.renderer.renderFiles(filesResp.files || []);
    } catch (err) {
      this.renderer.notify(err.message || "Erreur de rafraîchissement", true);
      this.renderer.pushLine("error-stream", err.message || "Erreur de rafraîchissement", this.renderer.errorLines, MAX_STREAM_LINES);
    }
  }

  async refreshHeartbeat() {
    try {
      const payload = await this.api.heartbeat();
      this.renderer.setHeartbeat(payload);
    } catch (err) {
      this.renderer.notify(err.message || "Heartbeat indisponible", true);
      const hbState = document.getElementById("hb-state");
      const hbDetails = document.getElementById("hb-details");
      const hbDot = document.getElementById("hb-dot");
      if (hbDot) hbDot.classList.add("fail");
      if (hbState) hbState.textContent = `heartbeat perdu: ${err.message || "erreur"}`;
      if (hbDetails) hbDetails.textContent = hbState?.textContent || "heartbeat perdu";
    }
  }

  async startRip(device, title) {
    if (!device) return;
    try {
      await this.api.startRip(device, title, this.state.mode);
      this.renderer.notify(`Extraction lancée en mode ${this.state.mode}: ${title}`);
      await this.refreshAll();
    } catch (err) {
      this.renderer.notify(err.message || "Impossible de lancer", true);
    }
  }

  async cancelRip(jobId) {
    if (!jobId) return;
    try {
      await this.api.cancelRip(jobId);
      this.renderer.notify(`Annulation demandée pour ${jobId}`);
      await this.refreshAll();
    } catch (err) {
      this.renderer.notify(err.message || "Impossible d'annuler", true);
    }
  }
}

new DashboardApp().init();
