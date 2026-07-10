"""Spotify for Artists client.

The public web-player token (open.spotify.com/api/token) is RBAC-denied by the
S4A insights backend. The S4A web app instead mints an artist-scoped token via a
PKCE exchange against accounts.spotify.com. Rather than reimplement that flow, we
drive a headless browser with the captured ``sp_dc`` session, let the real S4A
frontend mint the token, and intercept it. The token (~1h lifetime) is cached and
reused for direct httpx calls to the current ``song-stats-view`` endpoints.
"""

from __future__ import annotations

import logging
import threading
import time
from functools import lru_cache

import httpx

from app.config import settings

logger = logging.getLogger("album_tracker.s4a")

WG_BASE = "https://generic.wg.spotify.com"
TOKEN_TTL_S = 50 * 60


class S4AError(RuntimeError):
    pass


class _BrowserTokenProvider:
    """Mints an artist-scoped bearer token by intercepting the S4A frontend."""

    def __init__(self, sp_dc: str, artist_id: str) -> None:
        self._sp_dc = sp_dc
        self._artist_id = artist_id
        self._token: str | None = None
        self._minted_at = 0.0
        self._lock = threading.Lock()

    def token(self, force: bool = False) -> str:
        with self._lock:
            fresh = self._token and (time.monotonic() - self._minted_at) < TOKEN_TTL_S
            if fresh and not force:
                return self._token  # type: ignore[return-value]
            self._token = self._mint()
            self._minted_at = time.monotonic()
            return self._token

    def _mint(self) -> str:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise S4AError(
                "Browser support missing. Run: uv pip install 'spotifyscraper[browser]' "
                "&& python -m playwright install chromium"
            ) from exc

        captured: dict[str, str] = {}
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            context.add_cookies(
                [
                    {
                        "name": "sp_dc",
                        "value": self._sp_dc,
                        "domain": ".spotify.com",
                        "path": "/",
                        "httpOnly": True,
                        "secure": True,
                    }
                ]
            )
            page = context.new_page()

            def on_request(request):
                auth = request.headers.get("authorization", "")
                if auth.startswith("Bearer ") and "bearer" not in captured:
                    captured["bearer"] = auth[len("Bearer ") :]

            page.on("request", on_request)
            try:
                page.goto(
                    f"https://artists.spotify.com/c/artist/{self._artist_id}/home",
                    wait_until="networkidle",
                    timeout=60000,
                )
            except Exception:  # noqa: BLE001 - token may already be captured
                pass
            page.wait_for_timeout(2000)
            browser.close()

        token = captured.get("bearer")
        if not token:
            raise S4AError(
                "Could not mint a Spotify for Artists token. Your sp_dc session may be "
                "expired or lacks access to this artist. Re-run: python login.py"
            )
        return token


class S4AClient:
    def __init__(self, sp_dc: str, artist_id: str) -> None:
        self._provider = _BrowserTokenProvider(sp_dc, artist_id)
        self._artist_id = artist_id

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._provider.token()}",
            "Accept": "application/json",
            "Origin": "https://artists.spotify.com",
            "Referer": "https://artists.spotify.com/",
        }

    def _get_json(self, url: str) -> dict:
        for attempt in (1, 2):
            headers = self._headers()
            if attempt == 2:
                headers["Authorization"] = f"Bearer {self._provider.token(force=True)}"
            response = httpx.get(url, headers=headers, timeout=30.0)
            if response.status_code == 401 and attempt == 1:
                continue
            if response.status_code >= 400:
                raise S4AError(
                    f"S4A request failed ({response.status_code}) for {url}: {response.text[:200]}"
                )
            payload = response.json()
            if not isinstance(payload, dict):
                raise S4AError("Unexpected S4A response shape.")
            return payload
        raise S4AError("S4A auth failed after retry; re-run python login.py for a fresh session.")

    def get_total_streams(self, track_id: str) -> int:
        url = f"{WG_BASE}/song-stats-view/v2/artist/{self._artist_id}/recording/{track_id}/info"
        payload = self._get_json(url)
        raw = payload.get("total_stream_count", "0")
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0


@lru_cache(maxsize=1)
def get_s4a_client() -> S4AClient | None:
    sp_dc = settings.sp_dc.strip()
    artist_id = settings.artist_id.strip()
    if not sp_dc or not artist_id:
        return None
    return S4AClient(sp_dc, artist_id)


def s4a_configured() -> bool:
    return bool(settings.sp_dc.strip() and settings.artist_id.strip())


def fetch_track_streams(artist_id: str, track_ids: list[str]) -> dict[str, int]:
    client = get_s4a_client()
    if client is None:
        raise S4AError("Spotify for Artists is not configured (need SP_DC and ARTIST_ID).")

    streams: dict[str, int] = {}
    errors: list[str] = []
    for track_id in track_ids:
        try:
            streams[track_id] = client.get_total_streams(track_id)
        except S4AError as exc:
            errors.append(str(exc))
            logger.warning("S4A stream fetch failed for %s: %s", track_id, exc)

    if not streams:
        raise S4AError(errors[0] if errors else "No stream data returned from Spotify for Artists.")
    return streams
