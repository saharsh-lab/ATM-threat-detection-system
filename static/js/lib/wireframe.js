// static/js/lib/wireframe.js — restrained 3D wireframe ATM kiosk (canvas 2D,
// perspective projection, no dependencies). Colour follows the theme tokens and
// the current threat level; animation pauses off-screen and under
// prefers-reduced-motion it renders a single still frame.

const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

function box(cx, cy, cz, w, hh, d) {
  const x = w / 2, y = hh / 2, z = d / 2;
  const v = [[-x, -y, -z], [x, -y, -z], [x, y, -z], [-x, y, -z], [-x, -y, z], [x, -y, z], [x, y, z], [-x, y, z]]
    .map(([a, b, c]) => [a + cx, b + cy, c + cz]);
  const e = [[0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6], [6, 7], [7, 4], [0, 4], [1, 5], [2, 6], [3, 7]];
  return e.map(([i, j]) => [v[i], v[j]]);
}

function rect(cx, cy, z, w, hh) {   // flat rectangle on the front plane (z)
  const x = w / 2, y = hh / 2;
  const p = [[cx - x, cy - y, z], [cx + x, cy - y, z], [cx + x, cy + y, z], [cx - x, cy + y, z]];
  return [[p[0], p[1]], [p[1], p[2]], [p[2], p[3]], [p[3], p[0]]];
}

function buildATM() {
  const front = -0.3;
  const parts = {
    body: [...box(0, 0, 0, 0.72, 1.7, 0.6), ...box(0, -0.98, 0.02, 0.8, 0.26, 0.66)],   // cabinet + hood
    screen: rect(0, -0.42, front - 0.001, 0.46, 0.32),
    slots: [...rect(0.2, -0.05, front - 0.001, 0.16, 0.035), ...rect(0, 0.42, front - 0.001, 0.4, 0.05)],
    keypad: [],
    camera: [...box(0, -1.2, -0.12, 0.14, 0.08, 0.14)],
    floor: [],
  };
  for (let r = 0; r < 4; r++) for (let c = 0; c < 3; c++) {
    parts.keypad.push(...rect(-0.14 + c * 0.07, 0.04 + r * 0.06, front - 0.001, 0.05, 0.04));
  }
  for (let i = -5; i <= 5; i++) {
    parts.floor.push([[i * 0.25, 0.85, -1.25], [i * 0.25, 0.85, 1.25]], [[-1.25, 0.85, i * 0.25], [1.25, 0.85, i * 0.25]]);
  }
  return parts;
}

export function mountWireframe(canvas, { getLevel = () => "NORMAL" } = {}) {
  const ctx = canvas.getContext("2d");
  const parts = buildATM();
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let angle = -0.55, raf = null, visible = true, t0 = performance.now();

  const project = ([x, y, z], W, H, a) => {
    const ca = Math.cos(a), sa = Math.sin(a);
    const tilt = 0.32, ct = Math.cos(tilt), st = Math.sin(tilt);
    let X = x * ca - z * sa, Z = x * sa + z * ca;
    let Y = y * ct - Z * st; Z = y * st + Z * ct;
    const f = 3.4 / (Z + 4.2);
    const s = Math.min(W, H) * 0.34;
    return [W / 2 + X * f * s, H * 0.48 + Y * f * s, f];
  };

  function draw(now) {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const W = canvas.clientWidth, H = canvas.clientHeight;
    if (canvas.width !== W * dpr) { canvas.width = W * dpr; canvas.height = H * dpr; }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const level = getLevel();
    const hot = level === "HIGH" || level === "CRITICAL";
    const accent = hot ? css("--red") : level === "MEDIUM" ? css("--sev-medium") : css("--teal");
    const ink = css("--text-2");
    const t = (now - t0) / 1000;

    const stroke = (segs, color, width, alpha) => {
      ctx.strokeStyle = color; ctx.lineWidth = width; ctx.globalAlpha = alpha;
      ctx.beginPath();
      for (const [a, b] of segs) {
        const p = project(a, W, H, angle), q = project(b, W, H, angle);
        ctx.moveTo(p[0], p[1]); ctx.lineTo(q[0], q[1]);
      }
      ctx.stroke();
    };
    stroke(parts.floor, ink, 1, 0.12);
    stroke(parts.body, ink, 1.2, 0.55);
    stroke(parts.keypad, ink, 1, 0.4);
    stroke(parts.slots, accent, 1.4, 0.9);
    stroke(parts.screen, accent, 1.6, 0.95);
    stroke(parts.camera, css("--red"), 1.4, 0.9);

    // camera view cone (surveillance frustum) toward the user zone
    const apex = [0, -1.2, -0.21];
    const cone = [[-0.55, 0.85, -0.95], [0.55, 0.85, -0.95], [0.4, 0.85, -0.4], [-0.4, 0.85, -0.4]];
    ctx.setLineDash([3, 5]);
    stroke(cone.map((c) => [apex, c]), css("--red"), 1, 0.35);
    ctx.setLineDash([]);
    stroke(cone.map((c, i) => [c, cone[(i + 1) % 4]]), css("--red"), 1, hot ? 0.7 : 0.35);

    // sweeping scan plane
    const sy = -0.85 + ((reduced ? 0.5 : (Math.sin(t * 0.9) + 1) / 2) * 1.7);
    const plane = [[-0.42, sy, -0.36], [0.42, sy, -0.36], [0.42, sy, 0.36], [-0.42, sy, 0.36]];
    stroke(plane.map((p, i) => [p, plane[(i + 1) % 4]]), accent, 1.2, 0.5);
    ctx.globalAlpha = 1;

    if (!reduced) {
      angle += hot ? 0.0045 : 0.0025;
      if (visible && !document.hidden) raf = requestAnimationFrame(draw);
    }
  }

  const io = new IntersectionObserver(([e]) => {
    visible = e.isIntersecting;
    if (visible && !raf && !reduced) raf = requestAnimationFrame(draw);
    if (!visible && raf) { cancelAnimationFrame(raf); raf = null; }
  });
  io.observe(canvas);
  const onVis = () => { if (!document.hidden && visible && !reduced) { cancelAnimationFrame(raf); raf = requestAnimationFrame(draw); } };
  document.addEventListener("visibilitychange", onVis);
  const ro = new ResizeObserver(() => reduced && draw(performance.now()));
  ro.observe(canvas);
  raf = requestAnimationFrame(draw);
  return () => { cancelAnimationFrame(raf); io.disconnect(); ro.disconnect(); document.removeEventListener("visibilitychange", onVis); };
}
