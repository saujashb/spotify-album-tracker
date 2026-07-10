"""One-time helper: log into Spotify in a browser window and capture the sp_dc cookie.

Run:  python login.py

A Chromium window opens at the Spotify login page. Sign in with the account that
has Spotify for Artists access. Once you're logged in, this script grabs the
`sp_dc` cookie, writes it into `.env` as SP_DC, and closes the browser.

Nothing is typed for you and no password is ever read or stored — you log in by
hand, and only the resulting session cookie is captured.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ENV_PATH = Path(__file__).parent / ".env"


def _upsert_env(key: str, value: str) -> None:
    lines: list[str] = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()

    found = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")

    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    try:
        from spotify_scraper.browser import capture_sp_dc
    except ImportError:
        print(
            "Browser support is not installed. Run:\n"
            "  uv pip install 'spotifyscraper[browser]'\n"
            "  python -m playwright install chromium",
            file=sys.stderr,
        )
        return 1

    print("Opening a browser window. Log into Spotify for Artists there…")
    print("(Use the account with artist access. Do NOT click 'Log out' when done.)")

    try:
        captured = capture_sp_dc(timeout=600.0)
    except Exception as exc:  # noqa: BLE001
        print(f"\nLogin capture failed: {exc}", file=sys.stderr)
        return 1

    _upsert_env("SP_DC", captured.sp_dc)
    print("\nCaptured sp_dc cookie and saved it to .env")
    if captured.sp_dc_expires_ms:
        from datetime import datetime, timezone

        expires = datetime.fromtimestamp(
            captured.sp_dc_expires_ms / 1000, tz=timezone.utc
        )
        print(f"Cookie valid until roughly {expires:%Y-%m-%d}")
    print("\nNow restart the dashboard:")
    print("  uvicorn app.main:app --reload --port 8080")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
