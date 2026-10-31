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
import sys
from pathlib import Path

from websockets.asyncio.server import serve

HOST = "127.0.0.1"
PORT = 8000

HEARTBEAT_INTERVAL = 5 # seconds
TIMEOUT = HEARTBEAT_INTERVAL * 3

# Backup service configuration
SERVICE_HOST = "127.0.0.1"
SERVICE_PORT = 5000

BACKUP_SCRIPT = Path(__file__).resolve().parent / "backup_service_process.py"

RECOVERY_TIMEOUT = 10  # seconds
RECOVERY_CHECK_INTERVAL = 0.5


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

failover_started = False
backup_process = None


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
        

async def check_service_health():
    """Check whether the key-storage service can process requests."""
    writer = None

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(SERVICE_HOST, SERVICE_PORT),
            timeout=1.0
        )

        request = {
            "request_id": "health-check",
            "action": "LIST_KEYS",
            "client_id": "heartbeat_monitor",
            "timestamp": time.time()
        }

        writer.write((json.dumps(request) + "\n").encode())
        await asyncio.wait_for(writer.drain(), timeout=1.0)

        response = await asyncio.wait_for(
            reader.readline(),
            timeout=1.0
        )

        data = json.loads(response.decode())

        return (
            data.get("request_id") == "health-check"
            and data.get("status") == "OK"
            and data.get("action") == "LIST_KEYS"
        )

    except (
        OSError,
        TimeoutError,
        ValueError,
        json.JSONDecodeError
    ):
        return False

    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


async def trigger_failover():
    """Launch the backup service and verify its recovery."""
    global backup_process, failover_started
    
    try:
        logging.warning("[Failover Triggered] Starting recovery procedure.")

        # Avoid starting a conflicting primary if the old
        # service is still responding despite missing heartbeats.
        if await check_service_health():
            logging.error(
                "Failover aborted: original service is still responding. "
                "Possible heartbeat failure."
            )
            return

        if not BACKUP_SCRIPT.is_file():
            logging.error(
                "Backup script not found: %s",
                BACKUP_SCRIPT
            )
            return

        try:
            # Start backup as an independent OS process.
            backup_process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(BACKUP_SCRIPT),
                SERVICE_HOST,
                str(SERVICE_PORT)
            )

            logging.info(
                "Backup process launched with PID %d",
                backup_process.pid
            )

        except OSError as e:
            logging.error("Failed to launch backup: %s", e)
            return

        start_time = time.monotonic()

        # Wait until the backup begins processing requests.
        while time.monotonic() - start_time < RECOVERY_TIMEOUT:

            if backup_process.returncode is None:
                # Poll for process exit without blocking the monitor.
                try:
                    await asyncio.wait_for(
                        backup_process.wait(),
                        timeout=0.01
                    )
                except TimeoutError:
                    pass

            if backup_process.returncode is not None:
                logging.error(
                    "Backup process exited with code %s",
                    backup_process.returncode
                )
                return

            if await check_service_health():
                recovery_time = time.monotonic() - start_time

                logging.info(
                    "[Service Restored] Backup is operational. "
                    "Recovery took %.2f seconds.",
                    recovery_time
                )
                return

            await asyncio.sleep(RECOVERY_CHECK_INTERVAL)

        logging.error(
            "Backup failed to become operational within %d seconds.",
            RECOVERY_TIMEOUT
        )
    finally:
        failover_started = False
        logging.info("Failover operation finished.")


async def check_heartbeat():
    global server_alive, failover_started

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
            # Only attempt failover once.
            if not failover_started:
                failover_started = True
                asyncio.create_task(trigger_failover())


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
