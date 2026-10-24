# Implement an independent Monitor Process that runs separately from the critical
# service (separate processes/sockets). The monitored service and the monitor must
# communicate periodic liveness signals:
#  Option A (Push/Periodic Heartbeat): The critical service sends a "HEARTBEAT"
# datagram to the monitor every T milliseconds.
#  Option B (Pull/Ping-Ack): The monitor pings the service every T milliseconds,
# requiring a timely response.
#  Define an explicit timeout threshold (e.g., missing N consecutive beats or
# exceeding a sliding window timeout).
#  When the critical service crashes, the monitor must detect the failure, print an
# alert to standard output with timestamps, and log the incident.
#  The monitor must not crash when the monitored service dies.


import asyncio
import json
import logging
import time

from websockets.asyncio.server import serve

HOST = "127.0.0.1"
PORT = 8000

HEARTBEAT_INTERVAL = 5 # seconds
TIMEOUT = HEARTBEAT_INTERVAL * 3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("heartbeat.log"),
        logging.StreamHandler()
    ]
)

logging.getLogger("websockets.server").addFilter(
    lambda record: record.getMessage() not in {"connection open", "connection closed"}
)

last_heartbeat = None
server_alive = False
connected = False
monitor_started = None


async def receive_heartbeat(websocket):
    global last_heartbeat, server_alive, connected

    if connected:
        await websocket.close(
            code=1008,
            reason="A server is already being monitored"
        )
        return

    connected = True
    logging.info("Critical server connected.")

    try:
        await websocket.send(json.dumps({"type": "ready"}))
        async for message in websocket:
            try:
                data = json.loads(message)
            except (json.JSONDecodeError, TypeError):
                logging.warning("Invalid heartbeat message.")
                continue
            if not isinstance(data, dict):
                continue
            if data.get("type") != "heartbeat":
                continue

            # Update time using the monitor's clock.
            last_heartbeat = time.monotonic()

            if not server_alive:
                logging.info("Critical server is ONLINE.")
                server_alive = True

            logging.info("Heartbeat received: %s", data)

    except Exception as e:
        logging.warning("Connection error: %s", e)

    finally:
        connected = False
        logging.warning("Critical server disconnected.")


async def check_heartbeat():
    global server_alive

    while True:
        await asyncio.sleep(0.5)

        if last_heartbeat is None:
            elapsed = time.monotonic() - monitor_started

            if elapsed >= TIMEOUT:
                if server_alive is not False:
                    server_alive = False

                # No heartbeat has arrived yet.
                # Startup remains a separate state.
            continue

        elapsed = time.monotonic() - last_heartbeat

        if elapsed >= TIMEOUT and server_alive:
            server_alive = False

            logging.error(
                "ALERT: Critical server is DOWN! "
                "No heartbeat received for %.2f seconds.",
                elapsed
            )


async def main():
    global monitor_started

    monitor_started = time.monotonic()

    logging.info(
        "Heartbeat monitor listening on %s:%d",
        HOST, PORT
    )
    logging.info("Timeout threshold: %d seconds", TIMEOUT)

    async with serve(
        receive_heartbeat,
        HOST,
        PORT,
        ping_interval=None
    ):
        await check_heartbeat()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Heartbeat monitor stopped.")
