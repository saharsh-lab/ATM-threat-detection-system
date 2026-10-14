// static/js/login.js — sign-in form (no inline scripts: CSP script-src 'self').
import { mountWireframe } from "./lib/wireframe.js";

mountWireframe(document.getElementById("login-wireframe"));

const form = document.getElementById("login");
const err = document.getElementById("login-error");
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  err.textContent = "";
  const data = Object.fromEntries(new FormData(form));
  if (!data.username || !data.password) { err.textContent = "Enter your username and password."; return; }
  const btn = form.querySelector("button");
  btn.disabled = true;
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": document.body.dataset.csrf },
      body: JSON.stringify(data),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) { err.textContent = body.error || "Sign-in failed."; return; }
    location.href = "/";
  } catch {
    err.textContent = "Cannot reach the server.";
  } finally { btn.disabled = false; }
});
