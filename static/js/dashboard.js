// static/js/dashboard.js
// Advanced AI Surveillance Sentinel Dashboard:
// - Non-overlapping tactical alerts
// - Camera feed switcher with live chips
// - State-transition event logging engine
// - Dual-mode (Vercel Cloud Video + Canvas / Local OpenCV MJPEG)

const CIRCUMFERENCE = 2 * Math.PI * 56; // matches SVG r=56

const RISK_COLORS = {
  NORMAL:   "#00e676",
  LOW:      "#ffd600",
  MEDIUM:   "#ff9100",
  HIGH:     "#ff1744",
  CRITICAL: "#d50000",
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
const videoStream    = document.getElementById("video-stream");
const videoPlayer    = document.getElementById("video-player");
const cctvCanvas     = document.getElementById("cctv-canvas");
const ctx            = cctvCanvas ? cctvCanvas.getContext("2d") : null;
const videoSelect    = document.getElementById("video-select");
const switchBtn      = document.getElementById("switch-btn");
const camChips       = document.querySelectorAll(".cam-chip");

const policeAlert    = document.getElementById("police-alert");
const gtBadge        = document.getElementById("gt-badge");
const gtSecureBadge  = document.getElementById("gt-secure-badge");
const hudCamTitle    = document.getElementById("hud-cam-title");
const camActiveId    = document.getElementById("cam-active-id");
const evalStatus     = document.getElementById("eval-status");

const btnPlayPause   = document.getElementById("btn-play-pause");
const btnRestart     = document.getElementById("btn-restart");
const playbackTime   = document.getElementById("playback-time");
const progressFill   = document.getElementById("progress-fill");

const eventLogEl     = document.getElementById("event-log");
const logCountEl     = document.getElementById("log-count");
const btnClearLog    = document.getElementById("btn-clear-log");
const filterBtns     = document.querySelectorAll(".log-filter-btn");

// ── Application State ────────────────────────────────────────────────────
let allTimelines     = {};
let activeTimeline   = null;
let activeVideoName  = "28.mp4";
let isCloudMode      = false;
let animFrameId      = null;
let currentFilter    = "all";

// Persistent security events memory
let securityEvents   = [
  {
    id: 1,
    time: new Date().toLocaleTimeString(),
    type: "system",
    badge: "SYSTEM",
    message: "Sentinel AI Vision Engine v8.4 initialized. Optical feed connected."
  }
];

let lastThreatState  = {
  level: "NORMAL",
  score: 0,
  reasons: new Set(),
  persons: 0,
  bags: 0
};

// ── Security Event Logging Engine ────────────────────────────────────────
function logEvent(type, message) {
  const time = new Date().toLocaleTimeString();
  const badgeMap = {
    critical: "CRITICAL",
    warning:  "WARNING",
    info:     "INFO",
    system:   "SYSTEM"
  };
  const event = {
    id: Date.now() + Math.random(),
    time: time,
    type: type,
    badge: badgeMap[type] || "EVENT",
    message: message
  };

  securityEvents.unshift(event);
  if (securityEvents.length > 80) securityEvents.pop(); // keep recent 80

  renderEventLog();
}

function renderEventLog() {
  if (!eventLogEl) return;
  eventLogEl.innerHTML = "";

  const filtered = securityEvents.filter(ev => {
    if (currentFilter === "all") return true;
    if (currentFilter === "alert") return ev.type === "critical" || ev.type === "warning";
    if (currentFilter === "system") return ev.type === "system" || ev.type === "info";
    return true;
  });

  if (logCountEl) {
    logCountEl.textContent = `${securityEvents.length} EVENTS`;
  }

  if (filtered.length === 0) {
    eventLogEl.innerHTML = `<li class="log-item system"><span class="log-msg">No events matching filter.</span></li>`;
    return;
  }

  filtered.slice(0, 30).forEach(ev => {
    const li = document.createElement("li");
    li.className = `log-item ${ev.type}`;
    li.innerHTML = `
      <div class="log-meta-line">
        <span class="log-time">${ev.time}</span>
        <span class="log-badge ${ev.type}">${ev.badge}</span>
      </div>
      <span class="log-msg">${ev.message}</span>
    `;
    eventLogEl.appendChild(li);
  });
}

// ── State Transition Observer ────────────────────────────────────────────
function observeThreatTransitions(curScore, curLevel, curReasons, persons, bags) {
  const curSet = new Set(curReasons);

  // Check for newly triggered threat reasons
  for (const reason of curSet) {
    if (!lastThreatState.reasons.has(reason)) {
      const type = (curLevel === "CRITICAL" || curLevel === "HIGH") ? "critical" : "warning";
      logEvent(type, `${reason} flagged on Camera [${activeVideoName}]`);
    }
  }

  // Check for level escalation
  if (curLevel !== lastThreatState.level) {
    if ((curLevel === "CRITICAL" || curLevel === "HIGH") && lastThreatState.level !== "CRITICAL") {
      logEvent("critical", `🚨 Threat Escalation: Level changed to ${curLevel} (Score: ${curScore}/100)`);
    } else if (curLevel === "NORMAL" && lastThreatState.level !== "NORMAL") {
      logEvent("info", `Perimeter cleared: Risk normalized to ${curLevel} (Score: ${curScore}/100)`);
    }
  }

  // Check for person count shifts
  if (persons > 1 && lastThreatState.persons <= 1) {
    logEvent("warning", `Multiple Persons Alert: ${persons} individuals detected inside restricted ATM zone`);
  }

  lastThreatState = {
    level: curLevel,
    score: curScore,
    reasons: curSet,
    persons: persons,
    bags: bags
  };
}

// ── UI Updates ───────────────────────────────────────────────────────────
function updateDashboardUI(threatData) {
  if (!threatData) return;
  const { score, level, reasons, stats, ground_truth } = threatData;
  const color = RISK_COLORS[level] || "#00e676";

  // Score radial gauge
  const offset = CIRCUMFERENCE - (score / 100) * CIRCUMFERENCE;
  const ring = document.getElementById("ring-fill");
  if (ring) {
    ring.style.strokeDashoffset = offset;
    ring.style.stroke = color;
    ring.style.filter = `drop-shadow(0 0 8px ${color}88)`;
  }
  const scoreVal = document.getElementById("score-value");
  if (scoreVal) scoreVal.textContent = score;

  // Risk badge
  const badge = document.getElementById("risk-badge");
  if (badge) {
    badge.textContent = level;
    badge.className = `risk-badge ${level}`;
  }

  // Status tag
  if (evalStatus) {
    evalStatus.textContent = level === "NORMAL" ? "SECURE" : level;
    evalStatus.style.borderColor = color;
    evalStatus.style.color = color;
  }

  // Active Reasons
  const list = document.getElementById("reasons-list");
  if (list) {
    list.innerHTML = "";
    if (!reasons || reasons.length === 0) {
      list.innerHTML = '<li class="no-threat">🛡️ All perimeters secure. No threats detected.</li>';
    } else {
      reasons.forEach(r => {
        const li = document.createElement("li");
        li.innerHTML = `⚠️ <span>${r}</span>`;
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
    statGt.className   = `stat-num ${ground_truth ? "danger" : "safe"}`;
  }

  // Ground truth badge on video overlay
  if (ground_truth) {
    if (gtBadge) gtBadge.classList.remove("hidden");
    if (gtSecureBadge) gtSecureBadge.classList.add("hidden");
  } else {
    if (gtBadge) gtBadge.classList.add("hidden");
    if (gtSecureBadge) gtSecureBadge.classList.remove("hidden");
  }

  // Tactical Alert Banner (Only active on high threat / anomaly, at top, NEVER covers controls!)
  if (policeAlert) {
    if (ground_truth || level === "CRITICAL" || level === "HIGH") {
      policeAlert.classList.remove("hidden");
    } else {
      policeAlert.classList.add("hidden");
    }
  }

  // Observe and emit security log events
  observeThreatTransitions(score, level, reasons || [], stats?.persons ?? 0, stats?.bags ?? 0);
}

// ── Cloud / Web Video & Canvas Overlay Engine ─────────────────────────────
function initCloudMode() {
  isCloudMode = true;
  videoStream.style.display = "none";
  videoPlayer.style.display = "block";
  cctvCanvas.style.display  = "block";

  loadVideoCloud(activeVideoName);
  startCanvasLoop();
}

function loadVideoCloud(filename) {
  activeVideoName = filename;
  activeTimeline = allTimelines[filename] || null;

  // Update HUD labels
  if (hudCamTitle) hudCamTitle.textContent = `${filename} [AI CCTV STREAM]`;
  if (camActiveId) camActiveId.textContent = `CAM: ${filename.replace('.mp4','')}`;
  if (videoSelect) videoSelect.value = filename;

  // Update active chip UI
  camChips.forEach(chip => {
    if (chip.getAttribute("data-video") === filename) {
      chip.classList.add("active");
    } else {
      chip.classList.remove("active");
    }
  });

  logEvent("system", `Switched CCTV optical feed to Camera [${filename}]`);

  videoPlayer.src = `/static/videos/${filename}`;
  videoPlayer.currentTime = 0;
  videoPlayer.play().catch(e => {
    console.log("Auto-play blocked or waiting for user gesture:", e);
  });
}

function startCanvasLoop() {
  if (animFrameId) cancelAnimationFrame(animFrameId);

  function renderFrame() {
    if (isCloudMode && videoPlayer && !videoPlayer.paused && !videoPlayer.ended) {
      drawCanvasOverlay();
      updatePlaybackScrubber();
    }
    animFrameId = requestAnimationFrame(renderFrame);
  }
  animFrameId = requestAnimationFrame(renderFrame);
}

function drawCanvasOverlay() {
  if (!ctx || !videoPlayer.videoWidth || !videoPlayer.videoHeight) return;

  const vw = videoPlayer.videoWidth;
  const vh = videoPlayer.videoHeight;

  // Sync canvas dimensions
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
  const isDanger = closest.l === "HIGH" || closest.l === "CRITICAL" || closest.gt;
  ctx.strokeStyle = isDanger ? "rgba(255, 23, 68, 0.85)" : "rgba(0, 230, 118, 0.45)";
  ctx.lineWidth = 2;
  ctx.setLineDash([8, 5]);
  ctx.strokeRect(zx1, zy1, zw, zh);

  // Restricted zone corner label
  ctx.font = "bold 11px 'JetBrains Mono', monospace";
  ctx.fillStyle = isDanger ? "rgba(255, 23, 68, 0.9)" : "rgba(0, 230, 118, 0.7)";
  ctx.fillText("ZONE: ATM RESTRICTED BOUNDARY", zx1 + 6, zy1 + 16);
  ctx.restore();

  // Draw bounding boxes
  if (closest.boxes) {
    closest.boxes.forEach(b => {
      const [x1, y1, x2, y2] = b.bbox;
      const bw = x2 - x1;
      const bh = y2 - y1;

      let stroke = "#00e5ff";
      let fill   = "rgba(0, 229, 255, 0.12)";
      let tag    = b.label;

      if (b.type === "tampering") {
        stroke = "#ff1744";
        fill   = "rgba(255, 23, 68, 0.3)";
        tag    = `! TAMPERING (${b.label})`;
      } else if (b.type === "bag") {
        stroke = "#ff9100";
        fill   = "rgba(255, 145, 0, 0.2)";
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
      ctx.font = "bold 12px 'JetBrains Mono', monospace";
      const textWidth = ctx.measureText(tag).width;
      ctx.fillStyle = stroke;
      ctx.fillRect(x1, Math.max(0, y1 - 22), textWidth + 10, 22);

      ctx.fillStyle = "#000000";
      ctx.fillText(tag, x1 + 5, Math.max(16, y1 - 6));
    });
  }

  // Update telemetry UI in sync with video frames
  updateDashboardUI({
    score:        closest.s,
    level:        closest.l,
    reasons:      closest.r,
    stats: {
      persons: closest.p,
      bags:    closest.b,
      frame:   closest.f
    },
    ground_truth: closest.gt
  });
}

function updatePlaybackScrubber() {
  if (!videoPlayer) return;
  const cur = videoPlayer.currentTime || 0;
  const dur = videoPlayer.duration || 1;
  const pct = Math.min(100, (cur / dur) * 100);

  if (progressFill) progressFill.style.width = `${pct}%`;

  if (playbackTime) {
    const curM = String(Math.floor(cur / 60)).padStart(2, "0");
    const curS = String(Math.floor(cur % 60)).padStart(2, "0");
    const durM = String(Math.floor(dur / 60)).padStart(2, "0");
    const durS = String(Math.floor(dur % 60)).padStart(2, "0");
    playbackTime.textContent = `${curM}:${curS} / ${durM}:${durS}`;
  }
}

// ── Playback Controls ────────────────────────────────────────────────────
if (btnPlayPause) {
  btnPlayPause.addEventListener("click", () => {
    if (!videoPlayer) return;
    if (videoPlayer.paused) {
      videoPlayer.play();
      btnPlayPause.textContent = "⏸ Pause";
    } else {
      videoPlayer.pause();
      btnPlayPause.textContent = "▶ Play";
    }
  });
}

if (btnRestart) {
  btnRestart.addEventListener("click", () => {
    if (!videoPlayer) return;
    videoPlayer.currentTime = 0;
    videoPlayer.play();
    btnPlayPause.textContent = "⏸ Pause";
  });
}

// ── Camera Switcher Event Listeners ──────────────────────────────────────
camChips.forEach(chip => {
  chip.addEventListener("click", () => {
    const video = chip.getAttribute("data-video");
    if (isCloudMode) {
      loadVideoCloud(video);
      fetch("/switch_video", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: video })
      }).catch(() => {});
    } else {
      switchLocalVideo(video);
    }
  });
});

if (switchBtn) {
  switchBtn.addEventListener("click", () => {
    const video = videoSelect.value;
    if (isCloudMode) {
      loadVideoCloud(video);
      fetch("/switch_video", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: video })
      }).catch(() => {});
    } else {
      switchLocalVideo(video);
    }
  });
}

async function switchLocalVideo(video) {
  await fetch("/switch_video", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename: video })
  });
  activeVideoName = video;
  if (hudCamTitle) hudCamTitle.textContent = `${video} [LIVE LOCAL]`;
  if (camActiveId) camActiveId.textContent = `CAM: ${video.replace('.mp4','')}`;
  videoStream.src = "/video_feed?" + Date.now();
  logEvent("system", `Switched local OpenCV feed to ${video}`);
}

// ── Log Filtering & Clearing ─────────────────────────────────────────────
filterBtns.forEach(btn => {
  btn.addEventListener("click", () => {
    filterBtns.forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    currentFilter = btn.getAttribute("data-filter");
    renderEventLog();
  });
});

if (btnClearLog) {
  btnClearLog.addEventListener("click", () => {
    securityEvents = [];
    renderEventLog();
    logEvent("system", "Event audit trail cleared by operator.");
  });
}

// ── Initialization ───────────────────────────────────────────────────────
async function init() {
  renderEventLog();

  // Load precomputed timelines
  try {
    const res = await fetch("/static/data/threat_timelines.json");
    if (res.ok) {
      allTimelines = await res.json();
    }
  } catch (e) {
    console.warn("Could not load timelines:", e);
  }

  // Detect environment (Local vs Vercel)
  const testImg = new Image();
  testImg.src = "/video_feed";
  let streamWorking = false;

  testImg.onload = () => {
    streamWorking = true;
    videoStream.style.display = "block";
    videoPlayer.style.display = "none";
    cctvCanvas.style.display  = "none";
    videoStream.src = "/video_feed";
    logEvent("system", "Connected to local OpenCV video streaming server.");
    setInterval(fetchLocalThreatData, 1000);
    fetchLocalThreatData();
  };

  testImg.onerror = () => {
    // Vercel Cloud Mode
    initCloudMode();
  };

  setTimeout(() => {
    if (!streamWorking && !isCloudMode) {
      initCloudMode();
    }
  }, 1200);
}

async function fetchLocalThreatData() {
  if (isCloudMode) return;
  try {
    const res = await fetch("/threat_data");
    if (!res.ok) throw new Error("HTTP error " + res.status);
    const data = await res.json();
    updateDashboardUI(data);
  } catch (e) {
    if (!isCloudMode && Object.keys(allTimelines).length > 0) {
      initCloudMode();
    }
  }
}

init();