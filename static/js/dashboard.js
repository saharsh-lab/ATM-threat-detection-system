// static/js/dashboard.js
// Dual-mode ATM surveillance dashboard: supports both local MJPEG OpenCV streaming
// and zero-dependency Vercel cloud playback with live YOLOv8 canvas overlays.

const CIRCUMFERENCE = 2 * Math.PI * 50; // matches SVG r=50

const RISK_COLORS = {
  NORMAL:   "#00c800",
  LOW:      "#c8c800",
  MEDIUM:   "#ff8c00",
  HIGH:     "#dc143c",
  CRITICAL: "#8b0000",
};

// ── Clock ────────────────────────────────────────────────────────────────
function updateClock() {
  const now = new Date();
  document.getElementById("clock").textContent =
    now.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
setInterval(updateClock, 1000);
updateClock();

// ── DOM References ───────────────────────────────────────────────────────
const videoStream = document.getElementById("video-stream");
const videoPlayer = document.getElementById("video-player");
const cctvCanvas  = document.getElementById("cctv-canvas");
const ctx         = cctvCanvas ? cctvCanvas.getContext("2d") : null;
const videoSelect = document.getElementById("video-select");
const switchBtn   = document.getElementById("switch-btn");

let allTimelines   = {};
let activeTimeline = null;
let isCloudMode    = false;
let animFrameId    = null;

// ── UI Update Helpers ────────────────────────────────────────────────────
function updateDashboardUI(threatData) {
  if (!threatData) return;
  const { score, level, reasons, stats, ground_truth, log } = threatData;
  const color = RISK_COLORS[level] || "#00c800";

  // Score ring
  const offset = CIRCUMFERENCE - (score / 100) * CIRCUMFERENCE;
  const ring = document.getElementById("ring-fill");
  if (ring) {
    ring.style.strokeDashoffset = offset;
    ring.style.stroke = color;
  }
  const scoreVal = document.getElementById("score-value");
  if (scoreVal) scoreVal.textContent = score;

  // Risk badge
  const badge = document.getElementById("risk-badge");
  if (badge) {
    badge.textContent = level;
    badge.style.color      = color;
    badge.style.background = color + "22";
  }

  // Reasons
  const list = document.getElementById("reasons-list");
  if (list) {
    list.innerHTML = "";
    if (!reasons || reasons.length === 0) {
      list.innerHTML = '<li class="no-threat">No threats detected</li>';
    } else {
      reasons.forEach(r => {
        const li = document.createElement("li");
        li.textContent = r;
        list.appendChild(li);
      });
    }
  }

  // Detection stats
  if (stats) {
    document.getElementById("stat-persons").textContent = stats.persons ?? 0;
    document.getElementById("stat-bags").textContent    = stats.bags    ?? 0;
    document.getElementById("stat-frame").textContent   = stats.frame   ?? 0;
  }
  const statGt = document.getElementById("stat-gt");
  if (statGt) {
    statGt.textContent = ground_truth ? "ANOMALOUS" : "NORMAL";
    statGt.style.color = ground_truth ? "#dc143c" : "#00c800";
  }

  // Ground truth badge & Police alert
  const gtBadge     = document.getElementById("gt-badge");
  const policeAlert = document.getElementById("police-alert");

  if (ground_truth || level === "CRITICAL") {
    if (gtBadge && ground_truth) gtBadge.classList.remove("hidden");
    if (policeAlert) policeAlert.classList.remove("hidden");
  } else {
    if (gtBadge) gtBadge.classList.add("hidden");
    if (policeAlert && level !== "CRITICAL") policeAlert.classList.add("hidden");
  }

  // Event log
  const logEl = document.getElementById("event-log");
  if (logEl && log && log.length > 0) {
    logEl.innerHTML = "";
    log.forEach(entry => {
      const li = document.createElement("li");
      const timeStr = entry.time || new Date().toLocaleTimeString();
      const msgStr  = entry.message || entry;
      li.innerHTML = `<span class="log-time">${timeStr}</span>${msgStr}`;
      logEl.appendChild(li);
    });
  }
}

// ── Cloud / Web Video & Canvas Overlay Engine ─────────────────────────────
function initCloudMode() {
  isCloudMode = true;
  videoStream.style.display = "none";
  videoPlayer.style.display = "block";
  cctvCanvas.style.display  = "block";

  const initialVideo = videoSelect.value || "28.mp4";
  loadVideoCloud(initialVideo);
  startCanvasLoop();
}

function loadVideoCloud(filename) {
  activeTimeline = allTimelines[filename] || null;
  videoPlayer.src = `/static/videos/${filename}`;
  videoPlayer.play().catch(e => {
    console.log("Auto-play blocked, waiting for user gesture:", e);
  });
}

function startCanvasLoop() {
  if (animFrameId) cancelAnimationFrame(animFrameId);

  function renderFrame() {
    if (isCloudMode && videoPlayer && !videoPlayer.paused && !videoPlayer.ended) {
      drawCanvasOverlay();
    }
    animFrameId = requestAnimationFrame(renderFrame);
  }
  animFrameId = requestAnimationFrame(renderFrame);
}

function drawCanvasOverlay() {
  if (!ctx || !videoPlayer.videoWidth || !videoPlayer.videoHeight) return;

  const vw = videoPlayer.videoWidth;
  const vh = videoPlayer.videoHeight;

  // Match canvas internal resolution to actual video dimensions
  if (cctvCanvas.width !== vw || cctvCanvas.height !== vh) {
    cctvCanvas.width  = vw;
    cctvCanvas.height = vh;
  }

  ctx.clearRect(0, 0, vw, vh);

  if (!activeTimeline || !activeTimeline.timeline) return;

  const curTime = videoPlayer.currentTime;
  const frames = activeTimeline.timeline;

  // Find closest precomputed detection frame
  let closest = frames[0];
  let minDiff = 999999;
  for (let i = 0; i < frames.length; i++) {
    const diff = Math.abs(frames[i].t - curTime);
    if (diff < minDiff) {
      minDiff = diff;
      closest = frames[i];
    }
  }

  // Draw Restricted Zone (5% margin)
  const zx1 = vw * 0.05;
  const zy1 = vh * 0.05;
  const zw  = vw * 0.90;
  const zh  = vh * 0.90;

  ctx.save();
  ctx.strokeStyle = (closest.l === "NORMAL" || closest.l === "LOW") ? "rgba(0, 200, 0, 0.4)" : "rgba(220, 20, 60, 0.85)";
  ctx.lineWidth = 2;
  ctx.setLineDash([8, 4]);
  ctx.strokeRect(zx1, zy1, zw, zh);
  ctx.restore();

  // Draw bounding boxes
  if (closest.boxes) {
    closest.boxes.forEach(b => {
      const [x1, y1, x2, y2] = b.bbox;
      const bw = x2 - x1;
      const bh = y2 - y1;

      let stroke = "#00bfff";
      let fill   = "rgba(0, 191, 255, 0.15)";
      let tag    = b.label;

      if (b.type === "tampering") {
        stroke = "#dc143c";
        fill   = "rgba(220, 20, 60, 0.25)";
        tag    = `! TAMPERING (${b.label})`;
      } else if (b.type === "bag") {
        stroke = "#ff8c00";
        fill   = "rgba(255, 140, 0, 0.2)";
        tag    = `BAG: ${b.label}`;
      } else if (b.dwell > 0) {
        tag = `${b.label} [${b.dwell}s]`;
      }

      ctx.fillStyle = fill;
      ctx.fillRect(x1, y1, bw, bh);

      ctx.strokeStyle = stroke;
      ctx.lineWidth = 2.5;
      ctx.strokeRect(x1, y1, bw, bh);

      // Label background
      ctx.font = "bold 13px 'Courier New', monospace";
      const textWidth = ctx.measureText(tag).width;
      ctx.fillStyle = stroke;
      ctx.fillRect(x1, Math.max(0, y1 - 20), textWidth + 8, 20);

      ctx.fillStyle = "#ffffff";
      ctx.fillText(tag, x1 + 4, Math.max(14, y1 - 5));
    });
  }

  // Update telemetry UI
  updateDashboardUI({
    score:        closest.s,
    level:        closest.l,
    reasons:      closest.r,
    stats: {
      persons: closest.p,
      bags:    closest.b,
      frame:   closest.f
    },
    ground_truth: closest.gt,
    log:          closest.log
  });
}

// ── Local Mode Polling ───────────────────────────────────────────────────
async function fetchLocalThreatData() {
  if (isCloudMode) return;
  try {
    const res  = await fetch("/threat_data");
    if (!res.ok) throw new Error("HTTP error " + res.status);
    const data = await res.json();
    updateDashboardUI(data);
  } catch (e) {
    // If local endpoint fails or we are on Vercel without /video_feed, switch to cloud mode
    if (!isCloudMode && Object.keys(allTimelines).length > 0) {
      console.log("Switching to Vercel Cloud Mode...");
      initCloudMode();
    }
  }
}

// ── Initialization ───────────────────────────────────────────────────────
async function init() {
  // First load timelines for seamless cloud fallback
  try {
    const res = await fetch("/static/data/threat_timelines.json");
    if (res.ok) {
      allTimelines = await res.json();
    }
  } catch (e) {
    console.warn("Could not load timelines:", e);
  }

  // Test if local /video_feed is alive
  const testImg = new Image();
  testImg.src = "/video_feed";
  let streamWorking = false;

  testImg.onload = () => {
    streamWorking = true;
    videoStream.style.display = "block";
    videoPlayer.style.display = "none";
    cctvCanvas.style.display  = "none";
    videoStream.src = "/video_feed";
    setInterval(fetchLocalThreatData, 1000);
    fetchLocalThreatData();
  };

  testImg.onerror = () => {
    // Local MJPEG not available (we are on Vercel!)
    console.log("MJPEG feed not detected. Initializing Vercel Cloud Mode.");
    initCloudMode();
  };

  // 1.5s timeout: if image hasn't loaded, default to cloud mode
  setTimeout(() => {
    if (!streamWorking && !isCloudMode) {
      console.log("MJPEG timed out. Defaulting to Vercel Cloud Mode.");
      initCloudMode();
    }
  }, 1500);
}

// ── Video Switcher ───────────────────────────────────────────────────────
if (switchBtn) {
  switchBtn.addEventListener("click", async () => {
    const filename = videoSelect.value;
    if (isCloudMode) {
      loadVideoCloud(filename);
      // Notify backend if available
      fetch("/switch_video", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ filename }),
      }).catch(() => {});
    } else {
      await fetch("/switch_video", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ filename }),
      });
      videoStream.src = "/video_feed?" + Date.now();
    }
  });
}

init();