"""Real-time Spotify for Artists live stream-count feed.

For the first 7 days of a release, S4A exposes a per-recording live counter over a
websocket at ``artistinsights-realtime3.spotify.com``. Each socket pushes a plain
integer (the recording's live total stream count) every ~2 seconds. Auth is the
``sp_dc`` session cookie sent in the handshake — the same session used elsewhere.

We keep one persistent socket per track, hold the latest value in memory, and let
the API layer read a cheap in-memory snapshot (no upstream call per request).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import websockets

from app.config import settings

logger = logging.getLogger("album_tracker.live")

WS_BASE = "wss://artistinsights-realtime3.spotify.com/ws-web/recordings/total-streams"
ORIGIN = "https://artists.spotify.com"
RECONNECT_DELAY_S = 5.0


class LiveStreamFeed:
    def __init__(self) -> None:
        self._track_names: dict[str, str] = {}
        self._track_numbers: dict[str, int] = {}
        self._order: list[str] = []
        self._values: dict[str, int] = {}
        self._tasks: list[asyncio.Task] = []
        self._updated_at: datetime | None = None
        self._connected_tracks: set[str] = set()
        self._error: str | None = None
        self._running = False

    def configure(self, tracks: list[tuple[str, int, str]]) -> None:
        """tracks: list of (track_id, track_number, track_name)."""
        self._order = [t[0] for t in tracks]
        self._track_numbers = {t[0]: t[1] for t in tracks}
        self._track_names = {t[0]: t[2] for t in tracks}

    @property
    def enabled(self) -> bool:
        return bool(settings.sp_dc.strip()) and bool(self._order)

    async def start(self) -> None:
        if self._running or not self._order:
            return
        if not settings.sp_dc.strip():
            self._error = "SP_DC not configured — live feed unavailable."
            logger.warning(self._error)
            return
        self._running = True
        self._tasks = [
            asyncio.create_task(self._run_track(track_id)) for track_id in self._order
        ]
        logger.info("Live feed started for %d tracks", len(self._order))

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()

    async def _run_track(self, track_id: str) -> None:
        url = f"{WS_BASE}?AUDIO_TRACKS={track_id}"
        headers = {"Cookie": f"sp_dc={settings.sp_dc.strip()}"}
        while self._running:
            try:
                async with websockets.connect(
                    url,
                    additional_headers=headers,
                    origin=ORIGIN,
                    open_timeout=20,
                    ping_interval=20,
                ) as ws:
                    self._connected_tracks.add(track_id)
                    self._error = None
                    logger.info("Live socket open for track %s", track_id)
                    async for message in ws:
                        value = _parse_int(message)
                        if value is None:
                            continue
                        self._values[track_id] = value
                        self._updated_at = datetime.now(timezone.utc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect on any socket error
                self._error = str(exc)
                logger.warning("Live socket for %s dropped: %s", track_id, exc)
            finally:
                self._connected_tracks.discard(track_id)
            if self._running:
                await asyncio.sleep(RECONNECT_DELAY_S)

    def has_data(self) -> bool:
        return bool(self._values)

    def snapshot(self) -> dict:
        tracks = [
            {
                "track_id": track_id,
                "track_number": self._track_numbers.get(track_id, 0),
                "track_name": self._track_names.get(track_id, ""),
                "streams": self._values.get(track_id, 0),
            }
            for track_id in self._order
        ]
        total = sum(self._values.get(track_id, 0) for track_id in self._order)
        return {
            "connected": bool(self._connected_tracks),
            "tracks_connected": len(self._connected_tracks),
            "tracks_total": len(self._order),
            "have_all_values": len(self._values) == len(self._order) and bool(self._order),
            "error": self._error,
            "updated_at": self._updated_at.isoformat() if self._updated_at else None,
            "total_streams": total,
            "tracks": tracks,
        }


def _parse_int(message) -> int | None:
    if isinstance(message, bytes):
        try:
            message = message.decode("utf-8")
        except UnicodeDecodeError:
            return None
    try:
        return int(str(message).strip())
    except (TypeError, ValueError):
        return None


live_feed = LiveStreamFeed()
