# At least one team member should take on the role of an adversarial auditor to analyze
# and test vulnerabilities in the heartbeat channel:
#  Attack 1 – Heartbeat Spoofing: Can an external script transmit fake heartbeat
# messages to the monitor so that the monitor reports the service is healthy even
# after the critical process has crashed?
#  Attack 2 – Replay Attack/Tampering: If the heartbeat payload includes status data
# or a timestamp, can an attacker capture a valid packet and replay it indefinitely
# to mask downtime?

import argparse
import ast
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

MONITOR_URL = "ws://127.0.0.1:8000"


def capture_logged_heartbeat(log_path):
    """Retrieve one genuine payload previously recorded by the monitor."""
    marker = "Heartbeat received: "

    for line in reversed(Path(log_path).read_text().splitlines()):
        if marker not in line:
            continue

        # The monitor logs a Python dictionary, rather than JSON.
        payload = ast.literal_eval(line.split(marker, 1)[1])

        if isinstance(payload, dict) and payload.get("type") == "heartbeat":
            return json.dumps(payload)

    raise ValueError("No recorded heartbeat found; run the real service first.")

async def run_attack(args):
    replay_message = None
    if args.mode == "replay":
        replay_message = capture_logged_heartbeat(args.log)
        print(f"Captured payload: {replay_message}")

    async with connect(MONITOR_URL, ping_interval=None) as websocket:
        deadline = time.monotonic() + args.duration
        sequence = 1

        while time.monotonic() < deadline:
            if args.mode == "false-liveness":
                # Minimal spoofed heartbeat: no timestamp or sequence.
                message = json.dumps({"type": "heartbeat"})
            elif replay_message is not None:
                # Resend the captured payload unchanged.
                message = replay_message
            else:
                # heartbeat resembling the real service.
                message = json.dumps({
                    "type": "heartbeat",
                    "sequence": sequence,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

            await websocket.send(message)
            print(f"{args.mode}: {message}", flush=True)

            sequence += 1
            await asyncio.sleep(args.interval)

    print("Attack finished; watch for the monitor's timeout alert.")


def main():
    parser = argparse.ArgumentParser(description="Local heartbeat audit")
    parser.add_argument("mode", choices=["spoof", "replay", "false-liveness"])
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--log", default="heartbeat.log")
    args = parser.parse_args()

    if args.interval <= 0 or args.duration <= 0:
        parser.error("Interval and duration must be positive.")

    try:
        asyncio.run(run_attack(args))
    except KeyboardInterrupt:
        print("\nAttack stopped.")
    except (OSError, ValueError, SyntaxError, ConnectionClosed) as error:
        raise SystemExit(f"Audit failed: {error}") from error


if __name__ == "__main__":
    main()