const state = {
  counters: new Map(),
  pollIntervalMs: 120000,
  liveActive: false,
};

const LIVE_POLL_MS = 2000;
const DASHBOARD_POLL_MS = 15000;

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

async function startAutoRefresh() {
  await refreshLive();
  await refreshDashboard();
  setInterval(refreshLive, LIVE_POLL_MS);
  setInterval(refreshDashboard, DASHBOARD_POLL_MS);
}

startAutoRefresh();
