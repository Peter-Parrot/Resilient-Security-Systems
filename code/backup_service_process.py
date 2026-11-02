"""Passive warm standby: await promotion, restore checkpoint, then serve on port 5000."""

import argparse
import json
import logging
import math
import socket

from service_runtime import run_service

DEFAULT_CONTROL_PORT = 5001

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("backup_key_storage_daemon.log"), logging.StreamHandler()]
)


def wait_for_promotion(host, port):
    """Listen for a monitor promotion request; do not occupy the service port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
        listener.listen(5)
        logging.info("Backup in WARM STANDBY on %s:%d", host, port)
        while True:
            conn, addr = listener.accept()
            with conn:
                conn.settimeout(3)
                try:
                    raw = conn.makefile("rb").readline(4096)
                    request = json.loads(raw)
                    if isinstance(request, dict) and request.get("action") == "PROMOTE":
                        conn.sendall(b'{"status":"PROMOTING"}\n')
                        logging.warning("Promotion requested by %s", addr)
                        return
                    conn.sendall(b'{"status":"ERROR"}\n')
                except (OSError, ValueError, json.JSONDecodeError):
                    logging.warning("Invalid promotion request from %s", addr)


def main():
    parser = argparse.ArgumentParser(description="Key-storage passive standby")
    parser.add_argument("host", nargs="?", default="127.0.0.1")
    parser.add_argument("port", nargs="?", type=int, default=5000)
    parser.add_argument("--control-port", type=int, default=DEFAULT_CONTROL_PORT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--activate", action="store_true",
                      help="Start recovery immediately (cold standby fallback)")
    mode.add_argument("--standby", action="store_true",
                      help="Wait for monitor promotion (also the default)")
    parser.add_argument("--crash-probability", type=float, default=0.0)
    args = parser.parse_args()
    if not math.isfinite(args.crash_probability) or not 0 <= args.crash_probability <= 1:
        parser.error("Crash probability must be between 0 and 1")
    try:
        if not args.activate:
            wait_for_promotion(args.host, args.control_port)
        # A corrupt/missing checkpoint is fatal; do not advertise false recovery.
        run_service(args.host, args.port, role="backup", require_existing=True,
                    crash_probability=args.crash_probability)
    except (OSError, ValueError) as exc:
        logging.error("Backup activation failed: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
