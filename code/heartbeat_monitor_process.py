"""Heartbeat monitor and automated passive-redundancy failover controller."""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

from websockets.asyncio.server import serve

HOST = "127.0.0.1"
PORT = 8000
HEARTBEAT_INTERVAL = 5.0
TIMEOUT = HEARTBEAT_INTERVAL * 3

SERVICE_HOST = "127.0.0.1"
SERVICE_PORT = 5000
CONTROL_PORT = 5001
BACKUP_SCRIPT = Path(__file__).resolve().parent / "backup_service_process.py"
RECOVERY_TIMEOUT = 10.0
RECOVERY_CHECK_INTERVAL = 0.5
FAILOVER_RETRY_INTERVAL = 10.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("heartbeat.log"), logging.StreamHandler()]
)

last_heartbeat = None
server_alive = False
connected = False
failover_in_progress = False
failover_complete = False
last_failover_attempt = float("-inf")
backup_process = None


async def receive_heartbeat(websocket):
    global last_heartbeat, server_alive, connected
    if connected:
        await websocket.close(code=1008, reason="Heartbeat slot is already occupied")
        return
    connected = True
    logging.info("Heartbeat sender connected")
    try:
        await websocket.send(json.dumps({"type": "ready"}))
        async for message in websocket:
            try:
                data = json.loads(message)
            except (json.JSONDecodeError, TypeError):
                logging.warning("Discarded malformed heartbeat")
                continue
            if not isinstance(data, dict) or data.get("type") != "heartbeat":
                continue
            role = data.get("role", "unknown")
            last_heartbeat = time.monotonic()
            if not server_alive:
                server_alive = True
                logging.info("Critical service is ONLINE (role=%s)", role)
            logging.info("Heartbeat received: %s", data)
    except Exception as exc:
        logging.warning("Heartbeat connection closed: %s", exc)
    finally:
        connected = False
        logging.warning("Heartbeat sender disconnected")


async def check_service_health():
    """Return valid GET_STATE response from the process on the client-facing port."""
    writer = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(SERVICE_HOST, SERVICE_PORT), timeout=1
        )
        req = {"request_id": "health-check", "action": "GET_STATE",
               "client_id": "heartbeat_monitor"}
        writer.write((json.dumps(req) + "\n").encode())
        await asyncio.wait_for(writer.drain(), timeout=1)
        line = await asyncio.wait_for(reader.readline(), timeout=1)
        result = json.loads(line)
        if (isinstance(result, dict) and result.get("request_id") == "health-check"
            and result.get("status") == "OK" and result.get("action") == "GET_STATE"
            and isinstance(result.get("data"), dict)
            and type(result["data"].get("version")) is int):
            return result
    except (OSError, TimeoutError, ValueError, TypeError):
        pass
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
    return None


async def request_promotion():
    """Ask a running warm standby on port 5001 to activate."""
    writer = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(SERVICE_HOST, CONTROL_PORT), timeout=1
        )
        writer.write(b'{"action":"PROMOTE"}\n')
        await asyncio.wait_for(writer.drain(), timeout=1)
        line = await asyncio.wait_for(reader.readline(), timeout=2)
        return json.loads(line).get("status") == "PROMOTING"
    except (OSError, TimeoutError, ValueError, TypeError):
        return False
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


async def trigger_failover():
    global backup_process, failover_in_progress, failover_complete
    try:
        logging.warning("[Failover Triggered] Starting recovery procedure")
        # If primary still processes requests, an isolated heartbeat failure is
        # not proof of process failure. Avoid promoting a conflicting primary.
        healthy = await check_service_health()
        if healthy:
            logging.error("Failover blocked: a %s service still answers on port %d",
                          healthy.get("server_role", "unknown"), SERVICE_PORT)
            return

        if await request_promotion():
            logging.info("Warm standby accepted promotion request")
        else:
            if backup_process is not None and backup_process.returncode is None:
                logging.error("Backup process already running; will not launch duplicate")
                return
            if not BACKUP_SCRIPT.is_file():
                logging.error("Backup script is missing: %s", BACKUP_SCRIPT)
                return
            try:
                backup_process = await asyncio.create_subprocess_exec(
                    sys.executable, str(BACKUP_SCRIPT), SERVICE_HOST,
                    str(SERVICE_PORT), "--activate"
                )
            except OSError as exc:
                logging.error("Cannot launch backup: %s", exc)
                return
            logging.info("Cold backup process launched, PID %d", backup_process.pid)

        started = time.monotonic()
        while time.monotonic() - started < RECOVERY_TIMEOUT:
            if backup_process is not None and backup_process.returncode is None:
                try:
                    await asyncio.wait_for(backup_process.wait(), timeout=0.01)
                except TimeoutError:
                    pass
            if backup_process is not None and backup_process.returncode is not None:
                logging.error("Backup exited with status %s", backup_process.returncode)
                return

            response = await check_service_health()
            if response and response.get("server_role") == "backup":
                failover_complete = True
                logging.info("[Service Restored] Backup active; state_version=%d; MTTR=%.2fs",
                             response["data"]["version"], time.monotonic() - started)
                return
            await asyncio.sleep(RECOVERY_CHECK_INTERVAL)
        logging.error("Backup did not become healthy within %.1fs", RECOVERY_TIMEOUT)
    finally:
        failover_in_progress = False
        logging.info("Failover operation finished")


async def check_heartbeat():
    global server_alive, failover_in_progress, last_failover_attempt
    while True:
        await asyncio.sleep(0.5)
        if last_heartbeat is None:
            continue
        elapsed = time.monotonic() - last_heartbeat
        if elapsed < TIMEOUT:
            continue
        if server_alive:
            server_alive = False
            logging.error("[Crash Detected] No heartbeat received for %.2f seconds", elapsed)
        if failover_complete:
            # Only one backup is available. Still detect future outages,
            # but don't spawn a second replica automatically.
            continue
        now = time.monotonic()
        if (not failover_in_progress and
                now - last_failover_attempt >= FAILOVER_RETRY_INTERVAL):
            failover_in_progress = True
            last_failover_attempt = now
            asyncio.create_task(trigger_failover())


async def main():
    logging.info("Heartbeat monitor listening on %s:%d; timeout %.1fs", HOST, PORT, TIMEOUT)
    async with serve(receive_heartbeat, HOST, PORT, ping_interval=None):
        await check_heartbeat()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Heartbeat monitor stopped")
