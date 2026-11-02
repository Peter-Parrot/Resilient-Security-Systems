"""Critical key-storage service: primary node with heartbeat and checkpoints."""

import argparse
import logging
import math

from service_runtime import run_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("key_storage_daemon.log"), logging.StreamHandler()]
)


def main():
    parser = argparse.ArgumentParser(description="Key-storage primary service")
    parser.add_argument("host", nargs="?", default="127.0.0.1")
    parser.add_argument("port", nargs="?", type=int, default=5000)
    parser.add_argument("--crash-probability", type=float, default=0.1)
    parser.add_argument("--crash-interval", type=float, default=5.0)
    args = parser.parse_args()
    if not math.isfinite(args.crash_probability) or not 0 <= args.crash_probability <= 1:
        parser.error("Crash probability must be between 0 and 1")
    if not math.isfinite(args.crash_interval) or args.crash_interval <= 0:
        parser.error("Crash interval must be finite and positive")
    try:
        run_service(args.host, args.port, role="primary",
                    crash_probability=args.crash_probability,
                    crash_interval=args.crash_interval)
    except (OSError, ValueError) as exc:
        logging.error("Primary startup failed: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
