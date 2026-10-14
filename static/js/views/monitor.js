// Live Monitor — annotated feed, threat gauge, active signals, tracks.
// Live mode: server-side annotated MJPEG + /state polling.
// Demo mode: <video> + canvas overlay drawn from precomputed detections.
import { h, svg, clear, icon, fmt, sevBadge, srcBadge } from "../lib/dom.js";
import { lineChart, sevColor } from "../lib/charts.js";

const C = 2 * Math.PI * 56;
let timer = null, raf = null, current = null;
const history = [];

function gauge() {
  const fill = svg("circle", { class: "fill", cx: 70, cy: 70, r: 56, fill: "none", "stroke-width": 10, "stroke-linecap": "round",
    "stroke-dasharray": C, "stroke-dashoffset": C, transform: "rotate(-90 70 70)" });
  const num = svg("text", { class: "gauge-num", x: 70, y: 76, "text-anchor": "middle", text: "0" });
  const el = svg("svg", { viewBox: "0 0 140 140", role: "img", "aria-label": "Threat score" },
    svg("circle", { class: "track", cx: 70, cy: 70, r: 56, fill: "none", "stroke-width": 10 }), fill, num,
    svg("text", { class: "gauge-cap", x: 70, y: 96, "text-anchor": "middle", text: "THREAT / 100" }));
  return { el, set(score, level) {
    fill.setAttribute("stroke-dashoffset", C - (Math.max(0, Math.min(100, score)) / 100) * C);
    fill.setAttribute("stroke", sevColor(level));
    num.textContent = String(score);
    el.setAttribute("aria-label", `Threat score ${score} of 100, ${level}`);
  } };
}

export async function render(root, ctx) {
  const demo = ctx.mode === "demo";
  const { cameras } = await ctx.api.cameras();
  if (!cameras.length) {
    root.append(h("div", { class: "card empty" }, h("h2", { text: "No cameras configured" }),
      h("p", { text: "Copy config/cameras.example.yaml to config/cameras.yaml and restart the server." })));
    return;
  }
  const wanted = ctx.params.get("camera");
  current = cameras.find((c) => c.camera_id === wanted) || cameras[0];

  const tabs = h("div", { class: "cam-tabs", role: "tablist", "aria-label": "Cameras" });
  const feed = h("div", { class: "feed" });
  const hud = { src: h("span", { class: "chip" }), status: h("span", { class: "chip" }), perf: h("span", { class: "chip" }), gt: h("span", { class: "chip", hidden: true }) };
  const toolbar = h("div", { class: "feed-toolbar" });
  const g = gauge();
  const levelEl = h("div");
  const signals = h("ul", { class: "signal-list", "aria-live": "polite" });
  const kv = { persons: h("b", { text: "0" }), objects: h("b", { text: "0" }), fps: h("b", { text: "—" }), lat: h("b", { text: "—" }) };
  const chart = h("div");
  const tracks = h("div");
  const incidentEl = h("div", { class: "muted", style: { fontSize: "12.5px" } });

  root.append(tabs, h("div", { class: "grid g-main" },
    h("section", { class: "card", "aria-label": "Camera feed" }, feed, toolbar),
    h("section", { class: "card glass", "aria-label": "Threat intelligence" },
      h("div", { class: "card-head" }, h("h2", { text: "Threat intelligence" }), h("div", { class: "actions" }, levelEl)),
      h("div", { class: "gauge" }, g.el, h("div", {},
        h("div", { class: "muted", style: { fontSize: "12px", marginBottom: "6px" }, text: "Active signals (rule weight)" }), signals)),
      h("div", { class: "kv", style: { marginTop: "16px" } },
        h("div", {}, kv.persons, h("span", { text: "persons tracked" })), h("div", {}, kv.objects, h("span", { text: "objects" })),
        h("div", {}, kv.fps, h("span", { text: "inference fps" })), h("div", {}, kv.lat, h("span", { text: "latency ms" }))),
      h("div", { style: { marginTop: "14px" } }, incidentEl))));
  root.append(h("div", { class: "grid g-2" },
    h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "Threat score" }), h("span", { class: "sub", text: "rolling, this session" })), chart),
    h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "Tracks & objects" }), h("span", { class: "sub", text: "current frame" })), tracks)));

  function renderTabs() {
    clear(tabs);
    for (const c of cameras) {
      tabs.append(h("button", { class: "cam-tab", role: "tab", "aria-selected": String(c.camera_id === current.camera_id),
        onclick: () => { location.hash = `#/monitor?camera=${encodeURIComponent(c.camera_id)}`; } },
        h("span", { class: "status-dot", "data-s": c.status }),
        h("span", {}, c.name, h("small", { text: `${c.location || c.camera_id}` }))));
    }
  }
  renderTabs();

  function update(state) {
    const t = state.threat || { score: 0, level: "NORMAL", signals: [] };
    g.set(t.score, t.level);
    if (levelEl.dataset.level !== t.level) { levelEl.dataset.level = t.level; clear(levelEl).append(sevBadge(t.level)); }
    const sigs = t.signals?.length ? t.signals : (t.reasons || []).map((r) => ({ message: r }));
    const sigKey = sigs.map((x) => `${x.message}|${x.weight}`).join("§");
    if (signals.dataset.key !== sigKey) {          // re-render only on change (no animation flicker)
      signals.dataset.key = sigKey;
      clear(signals);
      if (!sigs.length) signals.append(h("li", { class: "quiet", text: "No active threat signals" }));
      for (const s of sigs) signals.append(h("li", {}, icon("alert"), h("span", { text: s.message }), s.weight ? h("span", { class: "w", text: `+${s.weight}` }) : null));
    }
    kv.persons.textContent = String(state.tracks?.length ?? 0);
    kv.objects.textContent = String(state.objects?.length ?? 0);
    kv.fps.textContent = state.infer_fps ? state.infer_fps.toFixed(1) : "—";
    kv.lat.textContent = state.latency_ms ? state.latency_ms.toFixed(0) : "—";
    if (state.ground_truth === null || state.ground_truth === undefined) hud.gt.hidden = true;
    else { hud.gt.hidden = false; hud.gt.textContent = state.ground_truth ? "RESEARCH LABEL: ANOMALY" : "RESEARCH LABEL: NORMAL"; }
    incidentEl.textContent = state.incident_id ? `Open incident ${state.incident_id}` : (demo ? "Demo data — no incidents are created." : "No open incident on this camera.");
    const tNow = state.media_time ?? Date.now() / 1000;
    if (history.length && tNow < history[history.length - 1].t) history.length = 0;   // replay restarted
    history.push({ t: tNow, s: t.score });
    if (history.length > 240) history.shift();
    lineChart(chart, { xs: history.map((x) => x.t), series: [{ name: "Threat score", color: sevColor(t.level === "NORMAL" ? "normal" : t.level), values: history.map((x) => x.s) }],
      yMax: 100, height: 170, format: (v) => `${Math.round(v)}`, xFormat: (x) => (x > 1e9 ? new Date(x * 1000).toLocaleTimeString() : `${Math.floor(x / 60)}:${String(Math.floor(x % 60)).padStart(2, "0")}`), area: true });
    const rows = [
      ...(state.tracks || []).map((p) => ["person", p.track_id ?? "—", p.confidence !== undefined ? p.confidence.toFixed(2) : "—",
        p.dwell_sec !== undefined ? `${p.dwell_sec.toFixed(0)}s` : "—",
        [p.loitering && "loitering", p.vandalism && "tampering", p.rapid_motion && "rapid motion"].filter(Boolean).join(", ") || "—"]),
      ...(state.objects || []).map((o) => [o.class_name, o.track_id ?? "—", o.confidence.toFixed(2), "—", "—"]),
    ];
    clear(tracks).append(rows.length ? h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ["Class", "Track ID", "Confidence", "Dwell", "Flags"].map((x, i) => h("th", { class: i && i < 4 ? "num" : "", text: x })))),
      h("tbody", {}, rows.map((r) => h("tr", {}, r.map((c, i) => h("td", { class: i && i < 4 ? "num" : "", text: String(c) }))))))) :
      h("p", { class: "empty", text: "Nothing detected in the current frame." }));
  }

  history.length = 0;
  if (demo) await startDemo(ctx, feed, hud, update);
  else startLive(ctx, feed, hud, toolbar, update);
}

function hudBar(feed, hud) {
  feed.append(h("div", { class: "feed-corner tl" }), h("div", { class: "feed-corner tr" }),
    h("div", { class: "feed-corner bl" }), h("div", { class: "feed-corner br" }),
    h("div", { class: "feed-hud" }, hud.src, hud.status, h("div", { class: "right" }, hud.gt, hud.perf)));
}

function startLive(ctx, feed, hud, toolbar, update) {
  const cam = current;
  const isFile = cam.source_type === "file";
  const img = h("img", { alt: `Annotated feed from ${cam.name}`, src: ctx.api.streamUrl(cam.camera_id) });
  const empty = h("div", { class: "feed-empty", hidden: true });
  feed.append(img, empty);
  hudBar(feed, hud);
  hud.src.textContent = isFile ? "● RECORDED REPLAY" : "● LIVE";
  img.addEventListener("error", () => { empty.hidden = false; empty.textContent = "Stream unavailable — retrying…"; setTimeout(() => { img.src = `${ctx.api.streamUrl(cam.camera_id)}?r=${Date.now()}`; }, 3000); });

  if (isFile && ctx.can("analyst")) {
    const sel = h("select", { class: "input", "aria-label": "Recorded video" });
    const upload = h("input", { type: "file", accept: "video/mp4,video/quicktime,video/x-msvideo,video/x-matroska", hidden: true });
    ctx.api.videos().then(({ videos }) => {
      for (const v of videos) sel.append(h("option", { value: v.name, text: `${v.name}${v.labelled ? (v.has_anomaly ? " · labelled anomalous" : " · labelled normal") : ""}`, selected: v.name === cam.video }));
    });
    toolbar.append(h("label", { class: "field", style: { flexDirection: "row", alignItems: "center", gap: "8px" } }, "Video", sel),
      h("button", { class: "btn", text: "Switch", onclick: async () => {
        try { await ctx.api.switchVideo(cam.camera_id, sel.value); ctx.toast(`Replaying ${sel.value}`); history.length = 0; }
        catch (e) { ctx.toast(e.message, "error"); } } }),
      h("button", { class: "btn", onclick: () => upload.click() }, icon("upload"), "Upload clip"), upload,
      h("span", { class: "muted", style: { fontSize: "12px" }, text: "Recorded files replay in real time and are labelled RECORDED in every incident." }));
    upload.addEventListener("change", async () => {
      const f = upload.files[0];
      if (!f) return;
      ctx.toast(`Uploading ${f.name}…`);
      try {
        const { video } = await ctx.api.uploadVideo(f);
        sel.append(h("option", { value: video, text: `${video} · uploaded`, selected: true }));
        ctx.toast("Upload verified — press Switch to analyse it.");
      } catch (e) { ctx.toast(e.message, "error"); }
    });
  }

  const poll = async () => {
    try {
      const s = await ctx.api.cameraState(cam.camera_id);
      hud.status.textContent = s.status.toUpperCase();
      hud.perf.textContent = `${s.fps ? s.fps.toFixed(0) : "—"} FPS · ${s.model?.id || "model"}`;
      const bad = ["offline", "error", "stopped", "starting"].includes(s.status);
      empty.hidden = !bad;
      if (bad) empty.textContent = s.error || (s.status === "starting" ? "Loading model…" : "Camera offline");
      update(s);
    } catch (e) {
      empty.hidden = false; empty.textContent = `State unavailable: ${e.message}`;
    }
  };
  poll();
  timer = setInterval(poll, 600);
}

async function startDemo(ctx, feed, hud, update) {
  const cam = current;
  const timelines = await ctx.api.timelines();
  const tl = timelines[cam.video];
  const video = h("video", { muted: true, playsinline: true, autoplay: true, loop: true, preload: "auto", src: `/static/videos/${cam.video}`, "aria-label": `Demo video ${cam.video}` });
  video.muted = true;
  const canvas = h("canvas", { "aria-hidden": "true" });
  feed.append(video, canvas);
  hudBar(feed, hud);
  hud.src.textContent = "DEMO · PRECOMPUTED";
  hud.status.textContent = cam.video;
  hud.perf.textContent = `${tl.meta?.model || "model"} · ${tl.meta?.engine || ""}`;
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  let lastIdx = -1;
  const frames = tl.timeline;

  const draw = () => {
    const t = video.currentTime;
    let lo = 0, hi = frames.length - 1;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (frames[mid].t <= t) lo = mid; else hi = mid - 1; }
    const fr = frames[lo];
    const W = canvas.clientWidth, H = canvas.clientHeight, dpr = Math.min(devicePixelRatio || 1, 2);
    if (canvas.width !== W * dpr) { canvas.width = W * dpr; canvas.height = H * dpr; }
    const c = canvas.getContext("2d");
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, W, H);
    const scale = Math.min(W / tl.width, H / tl.height), ox = (W - tl.width * scale) / 2, oy = (H - tl.height * scale) / 2;
    for (const b of fr?.boxes || []) {
      const [x1, y1, x2, y2] = b.bbox.map((v) => v * scale);
      const flagged = b.flag || ["knife", "firearm", "blunt_tool", "scissors"].includes(b.cls);
      c.strokeStyle = flagged ? css("--red") : b.cls === "person" ? css("--teal") : css("--sev-medium");
      c.lineWidth = 2;
      c.strokeRect(ox + x1, oy + y1, x2 - x1, y2 - y1);
      const label = b.cls === "person" ? `ID ${b.tid ?? "?"} ${b.conf?.toFixed(2) ?? ""} ${b.dwell ? `${Math.round(b.dwell)}s` : ""}` : `${b.cls} ${b.conf?.toFixed(2) ?? ""}`;
      c.font = "600 11px Inter, system-ui";
      const tw = c.measureText(label).width + 8;
      c.fillStyle = c.strokeStyle; c.fillRect(ox + x1, oy + y1 - 16, tw, 16);
      c.fillStyle = "#0D1014"; c.fillText(label, ox + x1 + 4, oy + y1 - 4);
    }
    if (lo !== lastIdx && fr) {
      lastIdx = lo;
      update({ threat: { score: fr.s, level: fr.l, reasons: fr.r, signals: fr.sig || [] }, tracks: (fr.boxes || []).filter((b) => b.cls === "person").map((b) => ({ track_id: b.tid, confidence: b.conf, dwell_sec: b.dwell || 0 })),
        objects: (fr.boxes || []).filter((b) => b.cls !== "person").map((b) => ({ class_name: b.cls, confidence: b.conf ?? 0 })),
        media_time: fr.t, ground_truth: fr.gt, infer_fps: tl.meta?.infer_fps, latency_ms: null });
    }
    raf = requestAnimationFrame(draw);
  };
  raf = requestAnimationFrame(draw);
}

export function destroy() { clearInterval(timer); cancelAnimationFrame(raf); timer = raf = null;
  document.querySelectorAll(".feed img").forEach((i) => { i.src = ""; });   // close MJPEG connection
}
