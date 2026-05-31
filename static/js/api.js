export class ApiClient {
  constructor(base = "") {
    this.base = base;
  }

  async fetchJSON(url, options = {}) {
    const response = await fetch(`${this.base}${url}`, options);
    const body = await response.text();

    if (!response.ok) {
      let message = body || `HTTP ${response.status}`;
      try {
        const payload = JSON.parse(body);
        if (payload && payload.error) {
          message = payload.error;
        }
      } catch (_err) {
        // keep raw message
      }
      throw new Error(message);
    }

    return body ? JSON.parse(body) : {};
  }

  async info() {
    return this.fetchJSON("/api/info");
  }

  async drives() {
    return this.fetchJSON("/api/drives");
  }

  async jobs() {
    return this.fetchJSON("/api/jobs");
  }

  async files() {
    return this.fetchJSON("/api/files");
  }

  async heartbeat() {
    return this.fetchJSON("/api/heartbeat");
  }

  async startRip(device, title, mode) {
    return this.fetchJSON("/api/rip", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device, title, mode }),
    });
  }

  async cancelRip(jobId) {
    const response = await fetch(`/api/rip/${encodeURIComponent(jobId)}`, { method: "DELETE" });
    const raw = await response.text();
    if (!response.ok) {
      let message = raw;
      try {
        const payload = JSON.parse(raw);
        if (payload && payload.error) message = payload.error;
      } catch (_err) {
        // keep raw body
      }
      throw new Error(message || `HTTP ${response.status}`);
    }
    return raw ? JSON.parse(raw) : {};
  }
}
