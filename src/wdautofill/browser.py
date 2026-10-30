"""Makes sure Playwright's Chromium is installed - so users never have to run
`playwright install chromium` themselves."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def chromium_installed() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            return Path(pw.chromium.executable_path).exists()
    except Exception:
        return False


def install_chromium() -> bool:
    print("Installing the Chromium browser that wdautofill drives (one time, about 150 MB)...")
    code = subprocess.call([sys.executable, "-m", "playwright", "install", "chromium"])
    if code != 0:
        print("Browser install failed. Try again, or run:  python -m playwright install chromium", file=sys.stderr)
        return False
    return chromium_installed()


def ensure_browser(channel: str | None = None) -> bool:
    """True when a browser is ready. `channel` (chrome / msedge) uses your installed browser instead."""
    if channel:
        return True
    return chromium_installed() or install_chromium()
