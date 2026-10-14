// static/js/lib/api.js — data access. Two sources with the same interface:
//   LiveAPI  → the Flask backend (/api/...), session cookie + CSRF header
//   DemoAPI  → read-only precomputed files under /static/data (serverless demo)
// Every incident / detection object carries `source_kind` so the UI can never
// present demo or recorded data as live.

const csrf = () => document.body.dataset.csrf || "";

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function request(method, url, body, { timeout = 15000, raw = false } = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  const opts = { method, signal: ctrl.signal, credentials: "same-origin", headers: {} };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  if (method !== "GET") opts.headers["X-CSRF-Token"] = csrf();
  let res;
  try {
    res = await fetch(url, opts);
  } catch (e) {
    throw new ApiError(0, e.name === "AbortError" ? "Request timed out" : "Network error — is the server running?");
  } finally { clearTimeout(timer); }
  if (res.status === 401 && !url.includes("/auth/")) { location.href = "/login"; throw new ApiError(401, "Signed out"); }
  if (raw) return res;
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) throw new ApiError(res.status, (data && data.error) || `Request failed (${res.status})`);
  return data;
}

export class LiveAPI {
  mode = "live";
  health() { return request("GET", "/api/health"); }
  me() { return request("GET", "/api/auth/me"); }
  logout() { return request("POST", "/api/auth/logout", {}); }
  platform() { return request("GET", "/api/platform"); }
  report(name) { return request("GET", `/api/reports/${encodeURIComponent(name)}`); }
  cameras() { return request("GET", "/api/cameras"); }
  cameraState(id) { return request("GET", `/api/cameras/${encodeURIComponent(id)}/state`, undefined, { timeout: 5000 }); }
  streamUrl(id) { return `/api/cameras/${encodeURIComponent(id)}/stream`; }
  switchVideo(id, video) { return request("POST", `/api/cameras/${encodeURIComponent(id)}/video`, { video }); }
  videos() { return request("GET", "/api/videos"); }
  uploadVideo(file) { const f = new FormData(); f.append("file", file); return request("POST", "/api/videos", f, { timeout: 300000 }); }
  incidents(params) { return request("GET", `/api/incidents?${new URLSearchParams(params)}`); }
  incident(id) { return request("GET", `/api/incidents/${encodeURIComponent(id)}`); }
  updateIncident(id, changes) { return request("PATCH", `/api/incidents/${encodeURIComponent(id)}`, changes); }
  exportUrl(params) { return `/api/incidents/export.csv?${new URLSearchParams(params)}`; }
  evidenceUrl(id) { return `/api/evidence/${encodeURIComponent(id)}`; }
  stats(hours) { return request("GET", `/api/stats/summary?hours=${hours}`); }
  alerts(sinceId) { return request("GET", `/api/alerts?since_id=${sinceId}`, undefined, { timeout: 8000 }); }
  settings() { return request("GET", "/api/settings"); }
  saveSettings(values) { return request("PUT", "/api/settings", values); }
  audit(params) { return request("GET", `/api/audit?${new URLSearchParams(params)}`); }
  backup() { return request("POST", "/api/admin/backup", {}); }
}

// ── demo source ──────────────────────────────────────────────────────────

const DEMO_CAMERAS = [
  { id: "demo-28", video: "28.mp4", name: "Demo feed A", location: "ATMA-V video 28" },
  { id: "demo-30a", video: "30a.mp4", name: "Demo feed B", location: "ATMA-V video 30a" },
  { id: "demo-12", video: "12.mp4", name: "Demo feed C", location: "ATMA-V video 12" },
  { id: "demo-57", video: "57.mp4", name: "Demo feed D", location: "ATMA-V video 57" },
  { id: "demo-3", video: "3.mp4", name: "Demo feed E", location: "ATMA-V video 3" },
];

export class DemoAPI {
  mode = "demo";
  #timelines = null;
  #platform = null;

  async #load() {
    if (!this.#timelines) {
      const [t, p] = await Promise.all([
        fetch("/static/data/threat_timelines.json").then((r) => (r.ok ? r.json() : {})),
        fetch("/static/data/platform.json").then((r) => (r.ok ? r.json() : {})).catch(() => ({})),
      ]);
      this.#timelines = t; this.#platform = p;
    }
  }
  async timelines() { await this.#load(); return this.#timelines; }
  async health() { return { status: "ok", mode: "demo" }; }
  async me() { return { user: { username: "demo", role: "viewer" } }; }
  async platform() { await this.#load(); return { mode: "demo", ...this.#platform, settings: [] }; }
  async report(name) {
    await this.#load();
    const r = (this.#platform.reports || {})[name];
    if (!r) throw new ApiError(404, "report not generated yet");
    return r;
  }
  async cameras() {
    await this.#load();
    return { cameras: DEMO_CAMERAS.filter((c) => this.#timelines[c.video]).map((c) => ({
      camera_id: c.id, name: c.name, location: c.location, status: "demo", source_type: "file",
      source_kind: "demo", video: c.video, meta: this.#timelines[c.video].meta || {} })) };
  }
  /** Incidents reconstructed from precomputed timelines (score ≥ 50 runs). */
  async #incidents() {
    await this.#load();
    const out = [];
    for (const cam of DEMO_CAMERAS) {
      const tl = this.#timelines[cam.video];
      if (!tl) continue;
      let cur = null;
      tl.timeline.forEach((fr, i) => {
        const on = fr.s >= 50;
        if (on && !cur) cur = { start: fr, peak: fr, reasons: new Set(), classes: new Set(), frames: [] };
        if (cur && on) {
          if (fr.s > cur.peak.s) cur.peak = fr;
          fr.r.forEach((r) => cur.reasons.add(r));
          (fr.boxes || []).forEach((b) => cur.classes.add(b.cls || b.type));
          cur.end = fr;
        }
        if (cur && (!on || i === tl.timeline.length - 1)) {
          out.push({
            id: `DEMO-${cam.video.replace(".mp4", "")}-${String(out.length + 1).padStart(2, "0")}`,
            camera_id: cam.id, source_kind: "demo", video_ref: cam.video,
            started_at: null, media_time_start: cur.start.t, media_time_end: cur.end.t,
            frame_start: cur.start.f, frame_end: cur.end.f, peak_score: cur.peak.s, threat_score: cur.end.s,
            severity: cur.peak.l === "NORMAL" ? "LOW" : cur.peak.l, status: "open",
            primary_reason: [...cur.reasons][0] || null, reasons: [...cur.reasons], rules: [],
            detection_classes: [...cur.classes], model_version: tl.meta?.model || "unknown",
            engine_version: tl.meta?.engine || "unknown", evidence_count: 0, detections: [], evidence: [], audit: [],
          });
          cur = null;
        }
      });
    }
    return out;
  }
  async incidents(params) {
    let items = await this.#incidents();
    const p = Object.fromEntries(new URLSearchParams(params));
    if (p.severity) items = items.filter((i) => p.severity.split(",").includes(i.severity));
    if (p.q) items = items.filter((i) => JSON.stringify(i).toLowerCase().includes(p.q.toLowerCase()));
    const per = Number(p.per_page || 25), page = Number(p.page || 1);
    return { items: items.slice((page - 1) * per, page * per), total: items.length, page, per_page: per };
  }
  async incident(id) { return (await this.#incidents()).find((i) => i.id === id); }
  async stats() {
    const items = await this.#incidents();
    const by = (k) => items.reduce((a, i) => ((a[i[k]] = (a[i[k]] || 0) + 1), a), {});
    return { total: items.length, open: items.length, avg_peak_score: items.length ? items.reduce((a, i) => a + i.peak_score, 0) / items.length : 0,
      by_severity: by("severity"), by_status: by("status"), by_camera: by("camera_id"), by_source: by("source_kind"),
      by_rule: items.flatMap((i) => i.reasons).reduce((a, r) => ((a[r.replace(/\s*\(.*\)$/, "")] = (a[r.replace(/\s*\(.*\)$/, "")] || 0) + 1), a), {}),
      hourly: [] };
  }
  async alerts() { return { alerts: [] }; }
  async settings() { return { settings: [] }; }
  async videos() { return { videos: [] }; }
  async logout() { location.href = "/"; }
  exportUrl() { return null; }
  evidenceUrl() { return null; }
}

export function createApi() {
  return document.body.dataset.mode === "demo" ? new DemoAPI() : new LiveAPI();
}
