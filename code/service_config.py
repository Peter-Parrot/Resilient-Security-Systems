"""Shared defaults; environment overrides apply to every project process."""
import os

SERVICE_HOST = os.environ.get("KEY_SERVICE_HOST", "127.0.0.1")
SERVICE_PORT = int(os.environ.get("KEY_SERVICE_PORT", "5050"))
CONTROL_PORT = int(os.environ.get("KEY_SERVICE_CONTROL_PORT", "5001"))
MONITOR_HOST = os.environ.get("KEY_SERVICE_MONITOR_HOST", "127.0.0.1")
MONITOR_PORT = int(os.environ.get("KEY_SERVICE_MONITOR_PORT", "8000"))
MONITOR_URI = f"ws://{MONITOR_HOST}:{MONITOR_PORT}"
