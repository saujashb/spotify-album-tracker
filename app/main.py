from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.config import settings
from app.live_feed import live_feed
from app.spotify_client import (
    AlbumMetadata,
    AlbumSnapshot,
    TrackSnapshot,
    configured_album_url,
    configured_data_source,
    fetch_album_metadata_async,
    fetch_album_snapshot_async,
)
from app.store import StreamStore, build_dashboard_payload

logger = logging.getLogger("album_tracker")
store = StreamStore()
poll_task: asyncio.Task | None = None
latest_error: str | None = None
album_meta: AlbumMetadata | None = None


def _snapshot_from_live(meta: AlbumMetadata) -> AlbumSnapshot:
    live = live_feed.snapshot()
    values = {t["track_id"]: t["streams"] for t in live["tracks"]}
    tracks = [
        TrackSnapshot(
            track_id=t.track_id,
            track_number=t.track_number,
            name=t.name,
            play_count=int(values.get(t.track_id, 0)),
        )
        for t in meta.tracks
    ]
    return AlbumSnapshot(
        album_id=meta.album_id,
        album_name=meta.album_name,
        artist_name=meta.artist_name,
        artist_id=meta.artist_id,
        cover_url=meta.cover_url,
        tracks=tracks,
        captured_at=datetime.now(timezone.utc),
        data_source="spotify_for_artists_live",
    )


async def poll_once() -> None:
    """Persist a history point. Prefer the live websocket feed; fall back to scrape."""
    global latest_error
    if live_feed.enabled:
        if not live_feed.has_data():
            return  # sockets still warming up; try again next tick
        snapshot = _snapshot_from_live(album_meta)  # type: ignore[arg-type]
    else:
        snapshot = await fetch_album_snapshot_async(configured_album_url())

    await store.save_snapshot(snapshot)
    latest_error = None
    logger.info(
        "Saved snapshot for %s (%s tracks, %s total streams)",
        snapshot.album_name,
        len(snapshot.tracks),
        f"{snapshot.total_streams:,}",
    )


async def poll_loop() -> None:
    global latest_error
    # Warm-up: wait for all track sockets to report before the first history point
    # so baselines aren't skewed by a track that hasn't ticked yet.
    warmup = 0
    while live_feed.enabled and not live_feed.snapshot()["have_all_values"] and warmup < 30:
        await asyncio.sleep(1)
        warmup += 1
    while True:
        try:
            await poll_once()
        except Exception as exc:  # noqa: BLE001 - surface poll failures in API/UI
            latest_error = str(exc)
            logger.exception("Poll failed: %s", exc)
        await asyncio.sleep(max(15, settings.poll_interval_seconds))


@asynccontextmanager
async def lifespan(_: FastAPI):
    global poll_task, album_meta
    await store.init()
    try:
        album_meta = await fetch_album_metadata_async(configured_album_url())
        live_feed.configure(
            [(t.track_id, t.track_number, t.name) for t in album_meta.tracks]
        )
        await live_feed.start()
    except Exception as exc:  # noqa: BLE001 - keep server up; surface in UI
        global latest_error
        latest_error = str(exc)
        logger.exception("Startup init failed: %s", exc)

    poll_task = asyncio.create_task(poll_loop())
    yield
    if poll_task:
        poll_task.cancel()
        try:
            await poll_task
        except asyncio.CancelledError:
            pass
    await live_feed.stop()


app = FastAPI(title="Spotify Album Stream Tracker", lifespan=lifespan)
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "dashboard.html")


@app.get("/api/health")
async def health() -> dict:
    return {
        "ok": latest_error is None,
        "error": latest_error,
        "poll_interval_seconds": settings.poll_interval_seconds,
        "data_source": configured_data_source(),
        "s4a_configured": bool(settings.sp_dc.strip()),
        "live": live_feed.snapshot(),
    }


@app.get("/api/live")
async def live_data() -> dict:
    """Cheap in-memory read of the real-time feed, safe to poll every ~2s."""
    live = live_feed.snapshot()
    live["album_name"] = album_meta.album_name if album_meta else None
    return live


@app.post("/api/poll")
async def manual_poll() -> dict:
    try:
        await poll_once()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True}


async def _album_id_from_config() -> str:
    album_url = configured_album_url()
    if "album/" not in album_url:
        raise ValueError("ALBUM_URL must be a Spotify album link.")
    return album_url.split("album/")[1].split("?")[0].strip("/")


@app.get("/api/dashboard")
async def dashboard_data() -> dict:
    try:
        album_id = await _album_id_from_config()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    latest = await store.latest_snapshot(album_id)
    if not latest:
        raise HTTPException(
            status_code=202,
            detail="First live snapshot is still arriving. Try again in a few seconds.",
        )

    previous = await store.previous_snapshot(album_id)
    first = await store.first_snapshot(album_id)
    history = await store.history(album_id)

    payload = build_dashboard_payload(
        latest=latest,
        previous=previous,
        first=first,
        history=history,
        poll_interval_seconds=settings.poll_interval_seconds,
        dashboard_title=settings.dashboard_title,
        data_source=configured_data_source(),
    )
    payload["health"] = {
        "ok": latest_error is None,
        "error": latest_error,
        "data_source": configured_data_source(),
        "live": live_feed.snapshot(),
    }
    return payload
