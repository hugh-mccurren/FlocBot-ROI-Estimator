"""
Background keep-alive ping for Streamlit hosting platforms.

Keeps the Streamlit process responsive by periodically hitting the local
health endpoint.  Works on both Streamlit Cloud (port 8501) and Render
(dynamic $PORT).

Pair with an external uptime monitor (e.g. UptimeRobot) that hits the
public URL every 5 minutes for reliable always-on behaviour.
"""

import os
import threading
import time
import urllib.request

_INTERVAL_SECONDS = 5 * 60  # 5 minutes
_PORT = os.environ.get("PORT", "8501")
_HEALTH_URL = f"http://localhost:{_PORT}/_stcore/health"
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
