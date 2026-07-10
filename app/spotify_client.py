from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from spotify_scraper import SpotifyClient

from app.config import settings
from app.s4a_client import S4AError, fetch_track_streams, s4a_configured


@dataclass
class TrackSnapshot:
    track_id: str
    track_number: int
    name: str
    play_count: int


@dataclass
class AlbumSnapshot:
    album_id: str
    album_name: str
    artist_name: str
    artist_id: str
    cover_url: str | None
    tracks: list[TrackSnapshot]
    captured_at: datetime
    data_source: str

    @property
    def total_streams(self) -> int:
        return sum(track.play_count for track in self.tracks)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _artist_name(artists: list[Any]) -> str:
    if not artists:
        return "Unknown Artist"
    first = artists[0]
    if hasattr(first, "name"):
        return first.name
    if isinstance(first, dict):
        return first.get("name", "Unknown Artist")
    return str(first)


def _artist_id(artists: list[Any]) -> str:
    if settings.artist_id.strip():
        return settings.artist_id.strip()
    if not artists:
        raise ValueError("Could not determine artist ID. Set ARTIST_ID in .env.")
    first = artists[0]
    if hasattr(first, "id"):
        return first.id
    if isinstance(first, dict):
        artist_id = first.get("id")
        if artist_id:
            return artist_id
    raise ValueError("Could not determine artist ID. Set ARTIST_ID in .env.")


def _image_url(images: list[Any]) -> str | None:
    if not images:
        return None
    first = images[0]
    if hasattr(first, "url"):
        return first.url
    if isinstance(first, dict):
        return first.get("url")
    return None


def fetch_album_snapshot(album_url: str) -> AlbumSnapshot:
    client = SpotifyClient()
    album = client.get_album(album_url)
    artist_id = _artist_id(getattr(album, "artists", []))
    track_refs = list(album.tracks)
    track_ids = [track_ref.id for track_ref in track_refs]

    if s4a_configured():
        streams_by_track = fetch_track_streams(artist_id, track_ids)
        data_source = "spotify_for_artists"
    else:
        streams_by_track = {}
        data_source = "public"

    tracks: list[TrackSnapshot] = []
    for index, track_ref in enumerate(track_refs, start=1):
        if data_source == "spotify_for_artists":
            play_count = int(streams_by_track.get(track_ref.id, 0))
            name = track_ref.name
            track_number = getattr(track_ref, "track_number", None) or index
        else:
            track = client.get_track(track_ref.id)
            play_count = int(track.play_count or 0)
            name = track.name
            track_number = getattr(track, "track_number", None) or index

        tracks.append(
            TrackSnapshot(
                track_id=track_ref.id,
                track_number=track_number,
                name=name,
                play_count=play_count,
            )
        )

    return AlbumSnapshot(
        album_id=album.id,
        album_name=album.name,
        artist_name=_artist_name(getattr(album, "artists", [])),
        artist_id=artist_id,
        cover_url=_image_url(getattr(album, "images", [])),
        tracks=tracks,
        captured_at=_utcnow(),
        data_source=data_source,
    )


async def fetch_album_snapshot_async(album_url: str) -> AlbumSnapshot:
    return await asyncio.to_thread(fetch_album_snapshot, album_url)


@dataclass
class AlbumMetadata:
    album_id: str
    album_name: str
    artist_name: str
    artist_id: str
    cover_url: str | None
    tracks: list[TrackSnapshot]  # play_count is 0 here; names/numbers/ids only


def fetch_album_metadata(album_url: str) -> AlbumMetadata:
    """Fast, auth-free album lookup for names/ids/cover (no per-track calls)."""
    client = SpotifyClient()
    album = client.get_album(album_url)
    artist_id = _artist_id(getattr(album, "artists", []))
    tracks: list[TrackSnapshot] = []
    for index, track_ref in enumerate(album.tracks, start=1):
        tracks.append(
            TrackSnapshot(
                track_id=track_ref.id,
                track_number=getattr(track_ref, "track_number", None) or index,
                name=track_ref.name,
                play_count=0,
            )
        )
    return AlbumMetadata(
        album_id=album.id,
        album_name=album.name,
        artist_name=_artist_name(getattr(album, "artists", [])),
        artist_id=artist_id,
        cover_url=_image_url(getattr(album, "images", [])),
        tracks=tracks,
    )


async def fetch_album_metadata_async(album_url: str) -> AlbumMetadata:
    return await asyncio.to_thread(fetch_album_metadata, album_url)


def configured_album_url() -> str:
    album_url = settings.album_url.strip()
    if not album_url or "YOUR_ALBUM_ID" in album_url:
        raise ValueError(
            "Set ALBUM_URL in .env to the Spotify album link you want to track."
        )
    return album_url


def configured_data_source() -> str:
    return "spotify_for_artists_live" if settings.sp_dc.strip() else "public"
