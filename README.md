# Spotify Album Stream Tracker

Live dashboard for tracking per-track Spotify play counts on a new album release.

## What artist access changes

Getting **Spotify for Artists** access is great for:

- Official per-track stream totals in the S4A web dashboard
- Saves, skip rates, listener geography, and **source of streams**
- Validating that your public play counts look right

It does **not** unlock a public API you can plug into this dashboard. Spotify has confirmed there is no Spotify for Artists API for stats.

So this project uses the **public play counts** Spotify shows on each track (via [SpotifyScraper](https://github.com/AliAkhtari78/SpotifyScraper)). Those are the right numbers for a release-night live counter. Use Spotify for Artists alongside this for the deeper private analytics.

## True real-time live feed (Spotify for Artists)

For the **first 7 days** of a release, Spotify for Artists shows a live stream counter that updates every ~2 seconds — even for tracks well below 1,000 streams (where public counts read 0). This dashboard consumes that exact feed.

Under the hood it connects to the S4A realtime websocket, one socket per track:

```
wss://artistinsights-realtime3.spotify.com/ws-web/recordings/total-streams?AUDIO_TRACKS=<trackId>
```

Each socket pushes a plain integer (that recording's live total) every couple of seconds. Auth is your `sp_dc` session cookie sent in the websocket handshake. The backend keeps the sockets open, holds the latest values in memory, and the page polls `/api/live` every 2 seconds to drive the big animated counter.

To enable it:

1. Capture your session cookie (opens a browser, you log in by hand):

```bash
python login.py
```

   Or set it manually — log into https://artists.spotify.com, DevTools → **Application** → **Cookies** → copy `sp_dc`.

2. Make sure `.env` has:

```env
SP_DC=your_sp_dc_value
ARTIST_ID=6EewwDHdBVYpE65rUV5taC
POLL_INTERVAL_SECONDS=60
```

3. Restart the server. The counter goes live within a couple of seconds.

Notes:
- `POLL_INTERVAL_SECONDS` only controls how often a **history** point is saved to SQLite (for the chart). The live counter itself is real-time regardless.
- After 7 days the realtime feed stops; the counter then reflects the last known live totals and the daily-aggregated stats.
- Treat `sp_dc` like a password — never commit it. It expires when you log out; re-run `python login.py` if the feed shows "reconnecting".

## Setup

1. Copy env file and add your album URL:

```bash
cp .env.example .env
```

Edit `.env`:

```env
ALBUM_URL=https://open.spotify.com/album/YOUR_ALBUM_ID
POLL_INTERVAL_SECONDS=120
```

For release night, try `60` or `90` seconds. Spotify does not update counts every second — expect changes every few minutes to hours depending on volume.

2. Create the Python environment and install deps:

```bash
uv python install 3.12
uv venv --python 3.12 .venv312
source .venv312/bin/activate
uv pip install -r requirements.txt
```

3. Run the dashboard:

```bash
uvicorn app.main:app --reload --port 8080
```

Open http://localhost:8080

## How it works

- With `SP_DC` set: persistent websockets to the S4A realtime feed give live per-track stream counts, pushed every ~2s. The dashboard polls `/api/live` and animates the central counter in real time.
- Without `SP_DC`: falls back to public play counts via [SpotifyScraper](https://github.com/AliAkhtari78/SpotifyScraper) (0 until ~1,000 streams).
- SQLite stores periodic snapshots so you can see deltas since last poll and since tracking started, plus the history chart.

## Validating against Spotify for Artists

After the first poll, compare one or two tracks in:

- This dashboard
- Spotify for Artists → Music → [track] → Streams

They should be very close. Small differences can happen because Spotify updates public counts and S4A on slightly different schedules.

## Deploy (share with others)

The app is containerized (`Dockerfile`) and works on any host that runs a container or a Python web service. It reads config from environment variables, so no `.env` file is needed in production.

Required env vars on the host:

| Variable | Value |
| --- | --- |
| `ALBUM_URL` | your Spotify album link |
| `ARTIST_ID` | your artist ID |
| `SP_DC` | your Spotify session cookie (secret) |
| `POLL_INTERVAL_SECONDS` | `60` |

### Render (recommended, has a free tier)

1. Push this repo to GitHub.
2. In Render: **New → Blueprint**, connect the repo. It reads `render.yaml`.
3. When prompted, paste `ALBUM_URL`, `ARTIST_ID`, and `SP_DC`.
4. Deploy. Share the `https://<name>.onrender.com` URL with your group.

### Anything else (Railway, Fly.io, Cloud Run, a VPS)

Build the image and run it, injecting the env vars. A `Procfile` is included for buildpack-style hosts.

```bash
docker build -t album-tracker .
docker run -p 8080:8080 \
  -e ALBUM_URL="https://open.spotify.com/album/…" \
  -e ARTIST_ID="…" \
  -e SP_DC="…" \
  -e POLL_INTERVAL_SECONDS=60 \
  album-tracker
```

Deployment notes:
- **`SP_DC` is your personal Spotify session** — set it as a *secret* env var, never commit it. Viewers of the dashboard only see the numbers, but the host holds your session. Refresh it when the feed shows "reconnecting".
- The live realtime feed only runs for a release's **first 7 days**.
- History is stored in SQLite on the local disk, which is **ephemeral** on most free hosts (resets on redeploy/restart). The live counter is unaffected; only the history chart resets. Attach a persistent disk if you want durable history.

## Notes

- No Spotify Developer app or artist login required for this approach
- Counts are cumulative lifetime streams, not "streams in the last hour"
- For a small brand-new release, numbers may sit at 0 for a while before Spotify surfaces public play counts
