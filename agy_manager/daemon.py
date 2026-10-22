"""Background daemon for Antigravity License Hub.

Periodically (every 30 minutes) refreshes OAuth tokens for all registered accounts,
ensuring zero logouts and up-to-date usage metrics.
"""

from __future__ import annotations

import logging
import signal
import sys
import time
from pathlib import Path

from .profiles import ProfileManager

LOG_FILE = Path.home() / ".config" / "agy-switcher" / "daemon.log"


def setup_logging():
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ]
    )


def run_single_refresh_pass(pm: ProfileManager):
    logging.info("Starting scheduled token refresh pass for all profiles...")
    results = pm.refresh_all()
    for pid, res in results.items():
        if res.get("success"):
            logging.info("  ✓ Profile [%s]: Token successfully refreshed", pid)
        else:
            logging.warning("  ✗ Profile [%s]: Refresh error: %s", pid, res.get("error"))
    logging.info("Refresh pass finished. All accounts active.")


def run_auto_rotate_check(pm: ProfileManager):
    """Check active profile quota and auto-switch to the best candidate if active is depleted."""
    try:
        res = pm.auto_rotate_if_needed(threshold_pct=10.0)
        if res.get("switched"):
            logging.warning(
                "⚡ [AUTO-ROTATE] Active license [%s] exhausted (%s). Switched to [%s] (%s)",
                res.get("previous_id"),
                res.get("reason"),
                res.get("new_id"),
                res.get("new_email"),
            )
    except Exception as e:
        logging.error("Error during auto-rotate check: %s", e)


def run_daemon(interval_seconds: int = 60, token_refresh_interval_sec: int = 3600):
    setup_logging()
    logging.info(
        "Starting AGY License Auto-Rotation Daemon (check every %ds, token & usage refresh every %ds / 1h)...",
        interval_seconds,
        token_refresh_interval_sec,
    )
    pm = ProfileManager()

    running = True
    def handle_sig(sig, frame):
        nonlocal running
        logging.info("Received stop signal. Shutting down daemon...")
        running = False

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    last_token_refresh = 0.0

    # Initial pass immediately on start
    try:
        run_single_refresh_pass(pm)
        last_token_refresh = time.time()
        run_auto_rotate_check(pm)
    except Exception as e:
        logging.error("Error during initial pass: %s", e)

    while running:
        # Sleep in short slices to respond quickly to signals
        for _ in range(interval_seconds):
            if not running:
                break
            time.sleep(1)

        if not running:
            break

        now = time.time()
        # 1. 60s Quota check and auto-rotation
        run_auto_rotate_check(pm)

        # 2. Periodic token refresh & live Google quota query pass (every 1 hour / 3600s)
        if now - last_token_refresh >= token_refresh_interval_sec:
            try:
                run_single_refresh_pass(pm)
                last_token_refresh = now
            except Exception as e:
                logging.error("Error during scheduled token refresh: %s", e)

    logging.info("AGY License Daemon stopped.")


if __name__ == "__main__":
    run_daemon(interval_seconds=60, token_refresh_interval_sec=3600)
