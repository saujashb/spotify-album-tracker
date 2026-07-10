const state = {
  counters: new Map(),
  pollIntervalMs: 120000,
  liveActive: false,
  velocitySamples: [], // {t, total}
  velocityHistory: [], // recent streams/min values for the sparkline
  celebrated: new Set(),
  lastMilestoneTotal: null,
};

const LIVE_POLL_MS = 2000;
const DASHBOARD_POLL_MS = 15000;
const VELOCITY_WINDOW_MS = 60000; // rate measured over the last minute
const VELOCITY_MIN_ELAPSED_MS = 10000; // need ~10s of data before showing a rate
const MILESTONES = [
  100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000, 250000, 500000,
  1000000,
];
let audioCtx = null;

function formatNumber(value) {
  return new Intl.NumberFormat().format(value ?? 0);
}

function formatDelta(value) {
  if (!value) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${formatNumber(value)}`;
}

function animateCounter(element, nextValue, options = {}) {
  const key = element.id || element.dataset.key;
  const hadPrevious = state.counters.has(key);
  const previous = state.counters.get(key) ?? nextValue;
  state.counters.set(key, nextValue);

  const duration = options.duration ?? 700;
  const start = performance.now();
  const from = previous;
  const to = nextValue;

  if (options.onIncrease && hadPrevious && to > from) {
    options.onIncrease(to - from);
  }

  function frame(now) {
    const progress = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - progress, 3);
    const current = Math.round(from + (to - from) * eased);
    element.textContent = formatNumber(current);
    if (progress < 1) requestAnimationFrame(frame);
  }

  requestAnimationFrame(frame);
}

function bumpCounter(element, delta) {
  element.classList.remove("bump");
  void element.offsetWidth;
  element.classList.add("bump");
  setTimeout(() => element.classList.remove("bump"), 320);

  const deltaEl = document.getElementById("total-delta");
  if (deltaEl) {
    deltaEl.innerHTML = `<span class="float-up">+${formatNumber(delta)} just now</span>`;
  }
}

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = value;
}

function renderTracks(tracks) {
  const body = document.getElementById("tracks-body");
  if (!tracks.length) {
    body.innerHTML = `<tr><td colspan="5" class="empty">No tracks found.</td></tr>`;
    return;
  }

  body.innerHTML = tracks
    .map((track) => {
      const lastClass =
        track.delta_since_last_poll > 0 ? "positive" : "neutral";
      const startClass =
        track.delta_since_tracking_started > 0 ? "positive" : "neutral";
      return `
        <tr>
          <td>${track.track_number}</td>
          <td>${track.track_name}</td>
          <td class="num mono" data-key="track-${track.track_id}">${formatNumber(track.play_count)}</td>
          <td class="num ${lastClass}">${formatDelta(track.delta_since_last_poll)}</td>
          <td class="num ${startClass}">${formatDelta(track.delta_since_tracking_started)}</td>
        </tr>
      `;
    })
    .join("");

  if (!state.liveActive) {
    tracks.forEach((track) => {
      const cell = body.querySelector(`[data-key="track-${track.track_id}"]`);
      if (cell) animateCounter(cell, track.play_count);
    });
  }
}

function drawHistory(history) {
  const canvas = document.getElementById("history-chart");
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  if (!history || history.length < 2) {
    ctx.fillStyle = "#9aa6b8";
    ctx.font = "14px Space Grotesk, sans-serif";
    ctx.fillText("History will appear after a few polls.", 12, 28);
    return;
  }

  const values = history.map((point) => point.total_streams);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(1, max - min);
  const padding = 18;

  ctx.strokeStyle = "rgba(255,255,255,0.08)";
  ctx.beginPath();
  ctx.moveTo(padding, height - padding);
  ctx.lineTo(width - padding, height - padding);
  ctx.stroke();

  ctx.strokeStyle = "#1ed760";
  ctx.lineWidth = 2;
  ctx.beginPath();
  history.forEach((point, index) => {
    const x =
      padding +
      (index / (history.length - 1)) * (width - padding * 2);
    const y =
      height -
      padding -
      ((point.total_streams - min) / range) * (height - padding * 2);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

async function fetchDashboard() {
  const response = await fetch("/api/dashboard");
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "Failed to load dashboard data.");
  }
  return data;
}

function renderDashboard(data) {
  const album = data.album;
  setText("title", data.title);
  setText("artist", album.artist_name);
  setText(
    "updated",
    `Last updated ${new Date(album.captured_at).toLocaleString()}`
  );
  setText(
    "poll-interval",
    state.liveActive
      ? "Real-time · live feed"
      : `Auto-refresh every ${Math.round(data.poll_interval_seconds / 60)} min`
  );
  setText("track-count", String(album.tracks.length));

  const totalEl = document.getElementById("total-streams");
  const deltaEl = document.getElementById("total-delta");
  if (!state.liveActive) {
    animateCounter(totalEl, album.total_streams, {
      duration: 1000,
      onIncrease: (delta) => bumpCounter(totalEl, delta),
    });
    if (!deltaEl.querySelector(".float-up")) {
      deltaEl.textContent =
        album.delta_since_last_poll > 0
          ? `+${formatNumber(album.delta_since_last_poll)} since last poll`
          : "Watching for new streams…";
    }
  }
  setText(
    "total-since-start",
    formatNumber(album.delta_since_tracking_started)
  );
  setText(
    "tracking-started",
    album.tracking_started_at
      ? `Tracking since ${new Date(album.tracking_started_at).toLocaleString()}`
      : "Tracking just started"
  );

  const cover = document.getElementById("cover");
  const placeholder = document.getElementById("cover-placeholder");
  if (album.cover_url) {
    cover.src = album.cover_url;
    cover.classList.remove("hidden");
    placeholder.classList.add("hidden");
  }

  renderTracks(album.tracks);
  drawHistory(data.history);

  const status = document.getElementById("status");
  if (data.health?.error) {
    status.textContent = `Latest poll error: ${data.health.error}`;
    status.classList.add("error");
  } else if (!state.liveActive) {
    status.textContent = "Counts are updating.";
    status.classList.remove("error");
  }

  const source = document.getElementById("source-note");
  if (source) {
    if (data.data_source === "spotify_for_artists_live") {
      source.textContent =
        "True real-time feed from Spotify for Artists — the same live stream counter S4A shows for the first 7 days, updating every ~2 seconds.";
    } else {
      source.textContent =
        "Public play counts only. Tracks below ~1,000 streams stay at 0 until Spotify publishes them. Add SP_DC to .env for the live S4A feed.";
    }
  }

  state.pollIntervalMs = (data.poll_interval_seconds || 120) * 1000;
}

function recordVelocity(total) {
  const now = Date.now();
  const samples = state.velocitySamples;
  const last = samples[samples.length - 1];
  if (!last || last.total !== total || now - last.t > 4000) {
    samples.push({ t: now, total });
  }
  const cutoff = now - 5 * 60 * 1000;
  while (samples.length > 2 && samples[0].t < cutoff) samples.shift();

  const first = samples[0];
  const elapsed = now - first.t;
  const pill = document.getElementById("velocity-pill");
  const stat = document.getElementById("velocity-stat");
  if (elapsed < VELOCITY_MIN_ELAPSED_MS) {
    if (stat) stat.textContent = "…";
    return;
  }

  // Base = the newest sample at least VELOCITY_WINDOW_MS old (else the oldest).
  let base = first;
  for (let i = samples.length - 1; i >= 0; i--) {
    if (now - samples[i].t >= VELOCITY_WINDOW_MS) {
      base = samples[i];
      break;
    }
  }
  const dtMin = (now - base.t) / 60000;
  const rate = dtMin > 0 ? Math.max(0, (total - base.total) / dtMin) : 0;
  const rounded = Math.round(rate);

  const valueEl = document.getElementById("velocity-value");
  if (valueEl) valueEl.textContent = formatNumber(rounded);
  if (stat) stat.textContent = formatNumber(rounded);
  if (pill) {
    pill.classList.remove("hidden");
    pill.classList.remove("pop");
    void pill.offsetWidth;
    pill.classList.add("pop");
    setTimeout(() => pill.classList.remove("pop"), 220);
  }

  state.velocityHistory.push(rate);
  if (state.velocityHistory.length > 120) state.velocityHistory.shift();
  drawVelocitySparkline();
}

function drawVelocitySparkline() {
  const canvas = document.getElementById("velocity-chart");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const data = state.velocityHistory;
  if (data.length < 2) return;

  const max = Math.max(1, ...data);
  const pad = 3;
  const stepX = (width - pad * 2) / (data.length - 1);

  const grad = ctx.createLinearGradient(0, 0, 0, height);
  grad.addColorStop(0, "rgba(30,215,96,0.35)");
  grad.addColorStop(1, "rgba(30,215,96,0)");

  ctx.beginPath();
  data.forEach((v, i) => {
    const x = pad + i * stepX;
    const y = height - pad - (v / max) * (height - pad * 2);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.lineTo(pad + (data.length - 1) * stepX, height);
  ctx.lineTo(pad, height);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  ctx.beginPath();
  data.forEach((v, i) => {
    const x = pad + i * stepX;
    const y = height - pad - (v / max) * (height - pad * 2);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.strokeStyle = "#1ed760";
  ctx.lineWidth = 2;
  ctx.stroke();
}

function checkMilestones(total) {
  if (state.lastMilestoneTotal === null) {
    // Baseline on first read so opening at an existing total doesn't fire.
    state.lastMilestoneTotal = total;
    MILESTONES.forEach((m) => {
      if (total >= m) state.celebrated.add(m);
    });
    return;
  }
  const prev = state.lastMilestoneTotal;
  for (const m of MILESTONES) {
    if (prev < m && total >= m && !state.celebrated.has(m)) {
      state.celebrated.add(m);
      celebrate(m);
    }
  }
  state.lastMilestoneTotal = total;
}

function celebrate(milestone) {
  showMilestoneBanner(milestone);
  fireConfetti();
  playChime();
}

function showMilestoneBanner(milestone) {
  const banner = document.getElementById("milestone-banner");
  if (!banner) return;
  banner.textContent = `🎉 ${formatNumber(milestone)} streams!`;
  banner.classList.remove("hidden");
  banner.classList.remove("show");
  void banner.offsetWidth;
  banner.classList.add("show");
  setTimeout(() => banner.classList.remove("show"), 4500);
}

function fireConfetti() {
  const canvas = document.getElementById("confetti");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const W = window.innerWidth;
  const H = window.innerHeight;
  canvas.width = W * dpr;
  canvas.height = H * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const colors = ["#1ed760", "#ffffff", "#4be38a", "#f7d000", "#ff6b6b", "#5aa9ff"];
  const parts = [];
  for (let i = 0; i < 180; i++) {
    parts.push({
      x: W / 2 + (Math.random() - 0.5) * 260,
      y: H * 0.28 + (Math.random() - 0.5) * 40,
      vx: (Math.random() - 0.5) * 10,
      vy: Math.random() * -9 - 3,
      size: Math.random() * 7 + 4,
      color: colors[(Math.random() * colors.length) | 0],
      rot: Math.random() * Math.PI,
      vr: (Math.random() - 0.5) * 0.35,
    });
  }

  const gravity = 0.24;
  const start = performance.now();
  function frame(now) {
    const t = now - start;
    ctx.clearRect(0, 0, W, H);
    parts.forEach((p) => {
      p.vy += gravity;
      p.x += p.vx;
      p.y += p.vy;
      p.rot += p.vr;
      ctx.save();
      ctx.translate(p.x, p.y);
      ctx.rotate(p.rot);
      ctx.globalAlpha = Math.max(0, 1 - t / 2800);
      ctx.fillStyle = p.color;
      ctx.fillRect(-p.size / 2, -p.size / 2, p.size, p.size * 0.6);
      ctx.restore();
    });
    if (t < 2800) requestAnimationFrame(frame);
    else ctx.clearRect(0, 0, W, H);
  }
  requestAnimationFrame(frame);
}

function playChime() {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    audioCtx = audioCtx || new Ctx();
    if (audioCtx.state === "suspended") audioCtx.resume();
    const notes = [523.25, 659.25, 783.99, 1046.5]; // C5 E5 G5 C6
    notes.forEach((freq, i) => {
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = "triangle";
      osc.frequency.value = freq;
      const t0 = audioCtx.currentTime + i * 0.1;
      gain.gain.setValueAtTime(0, t0);
      gain.gain.linearRampToValueAtTime(0.22, t0 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.001, t0 + 0.55);
      osc.connect(gain).connect(audioCtx.destination);
      osc.start(t0);
      osc.stop(t0 + 0.6);
    });
  } catch (err) {
    // Audio is best-effort; ignore if blocked.
  }
}

async function fetchLive() {
  const response = await fetch("/api/live");
  if (!response.ok) throw new Error("live feed unavailable");
  return response.json();
}

function renderLive(live) {
  if (!live) return;

  // Live feed owns the big counter + per-track stream cells once connected.
  if (live.connected && live.tracks_total > 0) {
    state.liveActive = true;
  }

  const totalEl = document.getElementById("total-streams");
  if (totalEl) {
    animateCounter(totalEl, live.total_streams, {
      duration: 800,
      onIncrease: (delta) => bumpCounter(totalEl, delta),
    });
  }

  if (typeof live.total_streams === "number") {
    recordVelocity(live.total_streams);
    checkMilestones(live.total_streams);
  }

  (live.tracks || []).forEach((track) => {
    const cell = document.querySelector(`[data-key="track-${track.track_id}"]`);
    if (cell) animateCounter(cell, track.streams);
  });

  const title = document.getElementById("title");
  if (title && live.album_name && title.textContent === "Loading album…") {
    title.textContent = `${live.album_name} — Live Streams`;
  }

  const updated = document.getElementById("updated");
  if (updated && live.updated_at) {
    updated.textContent = `Live · last tick ${new Date(
      live.updated_at
    ).toLocaleTimeString()}`;
  }

  const status = document.getElementById("status");
  if (status) {
    if (live.connected) {
      status.textContent = `Live feed connected (${live.tracks_connected}/${live.tracks_total} tracks) — updating every ~2s.`;
      status.classList.remove("error");
    } else if (live.error) {
      status.textContent = `Live feed reconnecting… ${live.error}`;
      status.classList.add("error");
    } else {
      status.textContent = "Connecting to live feed…";
      status.classList.remove("error");
    }
  }

  const deltaEl = document.getElementById("total-delta");
  if (deltaEl && !deltaEl.querySelector(".float-up") && !deltaEl.textContent) {
    deltaEl.textContent = "Watching for new streams…";
  }
}

async function refreshLive() {
  try {
    const live = await fetchLive();
    renderLive(live);
  } catch (error) {
    // Live endpoint is best-effort; ignore transient failures.
  }
}

async function refreshDashboard() {
  try {
    const data = await fetchDashboard();
    renderDashboard(data);
  } catch (error) {
    // Don't clobber the live feed UI while history is still warming up.
    if (state.liveActive) return;
    const status = document.getElementById("status");
    status.textContent = error.message;
    status.classList.add("error");
  }
}

document.getElementById("refresh-btn").addEventListener("click", async () => {
  const button = document.getElementById("refresh-btn");
  button.disabled = true;
  button.textContent = "Refreshing…";
  try {
    await fetch("/api/poll", { method: "POST" });
    await refreshDashboard();
  } catch (error) {
    const status = document.getElementById("status");
    status.textContent = error.message;
    status.classList.add("error");
  } finally {
    button.disabled = false;
    button.textContent = "Refresh now";
  }
});

function unlockAudio() {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    audioCtx = audioCtx || new Ctx();
    if (audioCtx.state === "suspended") audioCtx.resume();
  } catch (err) {
    // ignore
  }
  window.removeEventListener("pointerdown", unlockAudio);
  window.removeEventListener("keydown", unlockAudio);
}
window.addEventListener("pointerdown", unlockAudio);
window.addEventListener("keydown", unlockAudio);

// Debug helper: trigger a milestone celebration from the console.
window.testCelebrate = (m = 1000) => celebrate(m);

async function startAutoRefresh() {
  await refreshLive();
  await refreshDashboard();
  setInterval(refreshLive, LIVE_POLL_MS);
  setInterval(refreshDashboard, DASHBOARD_POLL_MS);
}

startAutoRefresh();
