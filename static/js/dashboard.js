// dashboard.js
// Polls /threat_data every second and updates all dashboard panels.

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

// ── Threat Data Polling ──────────────────────────────────────────────────
async function fetchThreatData() {
  try {
    const res  = await fetch("/threat_data");
    const data = await res.json();
    updateDashboard(data);
  } catch (e) {
    console.warn("Fetch error:", e);
  }
}

function updateDashboard(data) {
  const { score, level, reasons, stats, ground_truth, log } = data;
  const color = RISK_COLORS[level] || "#00c800";

  // Score ring
  const offset = CIRCUMFERENCE - (score / 100) * CIRCUMFERENCE;
  const ring = document.getElementById("ring-fill");
  ring.style.strokeDashoffset = offset;
  ring.style.stroke = color;
  document.getElementById("score-value").textContent = score;

  // Risk badge
  const badge = document.getElementById("risk-badge");
  badge.textContent = level;
  badge.style.color      = color;
  badge.style.background = color + "22";

  // Reasons
  const list = document.getElementById("reasons-list");
  list.innerHTML = "";
  if (reasons.length === 0) {
    list.innerHTML = '<li class="no-threat">No threats detected</li>';
  } else {
    reasons.forEach(r => {
      const li = document.createElement("li");
      li.textContent = r;
      list.appendChild(li);
    });
  }

  // Stats
  document.getElementById("stat-persons").textContent = stats.persons ?? 0;
  document.getElementById("stat-bags").textContent    = stats.bags    ?? 0;
  document.getElementById("stat-frame").textContent   = stats.frame   ?? 0;
  document.getElementById("stat-gt").textContent      = ground_truth ? "ANOMALOUS" : "NORMAL";
  document.getElementById("stat-gt").style.color      = ground_truth ? "#dc143c" : "#00c800";

  // Ground truth badge on video
  const gtBadge     = document.getElementById("gt-badge");
  const policeAlert = document.getElementById("police-alert");

  if (ground_truth) {
    gtBadge.classList.remove("hidden");
    policeAlert.classList.remove("hidden");
  } else {
    gtBadge.classList.add("hidden");
    policeAlert.classList.add("hidden");
  }
  // Event log
  const logEl = document.getElementById("event-log");
  if (log && log.length > 0) {
    logEl.innerHTML = "";
    log.forEach(entry => {
      const li = document.createElement("li");
      li.innerHTML = `<span class="log-time">${entry.time}</span>${entry.message}`;
      logEl.appendChild(li);
    });
  }
}

setInterval(fetchThreatData, 1000);
fetchThreatData();

// ── Video Switcher ───────────────────────────────────────────────────────
document.getElementById("switch-btn").addEventListener("click", async () => {
  const filename = document.getElementById("video-select").value;
  await fetch("/switch_video", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ filename }),
  });
  // Reload the video stream img
  const img = document.getElementById("video-stream");
  img.src = "/video_feed?" + Date.now();
});