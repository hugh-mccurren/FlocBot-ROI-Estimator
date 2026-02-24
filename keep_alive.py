"""
Background keep-alive ping for Streamlit Cloud.

Streamlit Cloud sleeps free-tier apps after ~7 days of no traffic (or sooner).
This module starts a daemon thread that pings the app's built-in health
endpoint every 5 minutes, helping keep the app awake as long as at least one
Streamlit server process is running.

NOTE: This alone is NOT enough — Streamlit Cloud can still sleep the entire
container if no *browser sessions* are open.  Pair this with an external
uptime monitor (e.g. UptimeRobot free tier) that hits your app URL every
5 minutes for reliable 24/7 uptime.
"""

import threading
import time
import urllib.request

_INTERVAL_SECONDS = 5 * 60  # 5 minutes
_HEALTH_URL = "https://localhost:8501/_stcore/health"
_started = False


def _ping_loop():
    """Quietly ping the local Streamlit health endpoint on a loop."""
    while True:
        time.sleep(_INTERVAL_SECONDS)
        try:
            urllib.request.urlopen(_HEALTH_URL, timeout=10)
        except Exception:
            pass  # swallow — the point is just to keep the process active


def start():
    """Start the keep-alive thread (idempotent — safe to call multiple times)."""
    global _started
    if _started:
        return
    _started = True
    t = threading.Thread(target=_ping_loop, daemon=True)
    t.start()
