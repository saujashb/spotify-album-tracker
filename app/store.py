from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from app.config import settings
from app.spotify_client import AlbumSnapshot, TrackSnapshot


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class StreamStore:
    def __init__(self, database_path: str | None = None) -> None:
        self.database_path = database_path or settings.database_path

    async def init(self) -> None:
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS album_meta (
                    album_id TEXT PRIMARY KEY,
                    album_name TEXT NOT NULL,
                    artist_name TEXT NOT NULL,
                    cover_url TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    album_id TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    total_streams INTEGER NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS track_snapshots (
                    snapshot_id INTEGER NOT NULL,
                    track_id TEXT NOT NULL,
                    track_number INTEGER NOT NULL,
                    track_name TEXT NOT NULL,
                    play_count INTEGER NOT NULL,
                    FOREIGN KEY(snapshot_id) REFERENCES snapshots(id)
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_snapshots_album_time ON snapshots(album_id, captured_at)"
            )
            await db.commit()

    async def save_snapshot(self, snapshot: AlbumSnapshot) -> int:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO album_meta (album_id, album_name, artist_name, cover_url, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(album_id) DO UPDATE SET
                    album_name = excluded.album_name,
                    artist_name = excluded.artist_name,
                    cover_url = excluded.cover_url,
                    updated_at = excluded.updated_at
                """,
                (
                    snapshot.album_id,
                    snapshot.album_name,
                    snapshot.artist_name,
                    snapshot.cover_url,
                    snapshot.captured_at.isoformat(),
                ),
            )
            cursor = await db.execute(
                """
                INSERT INTO snapshots (album_id, captured_at, total_streams)
                VALUES (?, ?, ?)
                """,
                (
                    snapshot.album_id,
                    snapshot.captured_at.isoformat(),
                    snapshot.total_streams,
                ),
            )
            snapshot_id = cursor.lastrowid
            await db.executemany(
                """
                INSERT INTO track_snapshots (
                    snapshot_id, track_id, track_number, track_name, play_count
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        snapshot_id,
                        track.track_id,
                        track.track_number,
                        track.name,
                        track.play_count,
                    )
                    for track in snapshot.tracks
                ],
            )
            await db.commit()
            return int(snapshot_id)

    async def latest_snapshot(self, album_id: str) -> dict | None:
        async with aiosqlite.connect(self.database_path) as db:
            db.row_factory = aiosqlite.Row
            row = await (
                await db.execute(
                    """
                    SELECT s.id, s.captured_at, s.total_streams, m.album_name, m.artist_name, m.cover_url
                    FROM snapshots s
                    JOIN album_meta m ON m.album_id = s.album_id
                    WHERE s.album_id = ?
                    ORDER BY s.captured_at DESC
                    LIMIT 1
                    """,
                    (album_id,),
                )
            ).fetchone()
            if not row:
                return None

            track_rows = await (
                await db.execute(
                    """
                    SELECT track_id, track_number, track_name, play_count
                    FROM track_snapshots
                    WHERE snapshot_id = ?
                    ORDER BY track_number ASC
                    """,
                    (row["id"],),
                )
            ).fetchall()

            return {
                "album_id": album_id,
                "album_name": row["album_name"],
                "artist_name": row["artist_name"],
                "cover_url": row["cover_url"],
                "captured_at": row["captured_at"],
                "total_streams": row["total_streams"],
                "tracks": [dict(track) for track in track_rows],
            }

    async def previous_snapshot(self, album_id: str) -> dict | None:
        async with aiosqlite.connect(self.database_path) as db:
            db.row_factory = aiosqlite.Row
            row = await (
                await db.execute(
                    """
                    SELECT s.id, s.captured_at, s.total_streams
                    FROM snapshots s
                    WHERE s.album_id = ?
                    ORDER BY s.captured_at DESC
                    LIMIT 1 OFFSET 1
                    """,
                    (album_id,),
                )
            ).fetchone()
            if not row:
                return None

            track_rows = await (
                await db.execute(
                    """
                    SELECT track_id, track_number, track_name, play_count
                    FROM track_snapshots
                    WHERE snapshot_id = ?
                    ORDER BY track_number ASC
                    """,
                    (row["id"],),
                )
            ).fetchall()

            return {
                "captured_at": row["captured_at"],
                "total_streams": row["total_streams"],
                "tracks": [dict(track) for track in track_rows],
            }

    async def first_snapshot(self, album_id: str) -> dict | None:
        async with aiosqlite.connect(self.database_path) as db:
            db.row_factory = aiosqlite.Row
            row = await (
                await db.execute(
                    """
                    SELECT s.id, s.captured_at, s.total_streams
                    FROM snapshots s
                    WHERE s.album_id = ?
                    ORDER BY s.captured_at ASC
                    LIMIT 1
                    """,
                    (album_id,),
                )
            ).fetchone()
            if not row:
                return None

            track_rows = await (
                await db.execute(
                    """
                    SELECT track_id, track_number, track_name, play_count
                    FROM track_snapshots
                    WHERE snapshot_id = ?
                    ORDER BY track_number ASC
                    """,
                    (row["id"],),
                )
            ).fetchall()

            return {
                "captured_at": row["captured_at"],
                "total_streams": row["total_streams"],
                "tracks": [dict(track) for track in track_rows],
            }

    async def history(self, album_id: str, limit: int = 200) -> list[dict]:
        async with aiosqlite.connect(self.database_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """
                    SELECT captured_at, total_streams
                    FROM snapshots
                    WHERE album_id = ?
                    ORDER BY captured_at ASC
                    LIMIT ?
                    """,
                    (album_id, limit),
                )
            ).fetchall()
            return [dict(row) for row in rows]


def build_dashboard_payload(
    latest: dict,
    previous: dict | None,
    first: dict | None,
    history: list[dict],
    poll_interval_seconds: int,
    dashboard_title: str,
    data_source: str,
) -> dict:
    previous_by_track = {
        track["track_id"]: track["play_count"]
        for track in (previous or {}).get("tracks", [])
    }
    first_by_track = {
        track["track_id"]: track["play_count"]
        for track in (first or {}).get("tracks", [])
    }

    tracks = []
    for track in latest["tracks"]:
        track_id = track["track_id"]
        current = track["play_count"]
        since_last = current - previous_by_track.get(track_id, current)
        since_start = current - first_by_track.get(track_id, current)
        tracks.append(
            {
                **track,
                "delta_since_last_poll": since_last,
                "delta_since_tracking_started": since_start,
            }
        )

    total_since_last = latest["total_streams"] - (previous or {}).get(
        "total_streams", latest["total_streams"]
    )
    total_since_start = latest["total_streams"] - (first or {}).get(
        "total_streams", latest["total_streams"]
    )

    return {
        "title": dashboard_title or f"{latest['album_name']} — Live Streams",
        "album": {
            "id": latest["album_id"],
            "name": latest["album_name"],
            "artist_name": latest["artist_name"],
            "cover_url": latest["cover_url"],
            "captured_at": latest["captured_at"],
            "total_streams": latest["total_streams"],
            "delta_since_last_poll": total_since_last,
            "delta_since_tracking_started": total_since_start,
            "tracking_started_at": (first or {}).get("captured_at"),
            "tracks": tracks,
        },
        "history": history,
        "poll_interval_seconds": poll_interval_seconds,
        "data_source": data_source,
    }
