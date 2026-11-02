"""Shared TCP key-vault service and heartbeat sender for both primary and backup."""

import asyncio
import json
import logging
import os
import random
import socketserver
import threading
import time

from websockets.asyncio.client import connect

from vault_state import VaultState

MONITOR_URI = "ws://127.0.0.1:8000"
HEARTBEAT_INTERVAL = 5.0
CHECKPOINT_INTERVAL = 5.0
MAX_LINE_SIZE = 64 * 1024


class VaultTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr, vault, role):
        self.vault = vault
        self.role = role
        super().__init__(addr, VaultHandler)


class VaultHandler(socketserver.StreamRequestHandler):
    """Handle a persistent client connection using newline-delimited JSON."""

    def handle(self):
        while True:
            try:
                line = self.rfile.readline(MAX_LINE_SIZE + 1)
                if not line:
                    return
                if len(line) > MAX_LINE_SIZE:
                    logging.warning("Client message too large; closing connection")
                    return
                try:
                    request = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    response = {
                        "request_id": None,
                        "status": "ERROR",
                        "error": "MALFORMED_JSON",
                        "message": "Request must be valid JSON"
                    }
                else:
                    response = self.server.vault.handle_request(request)

                response["server_role"] = self.server.role
                self.wfile.write((json.dumps(response) + "\n").encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return


async def send_heartbeats(vault, role):
    """Connect to monitor and transmit heartbeat messages while serving clients."""
    sequence = 0
    while True:
        try:
            logging.info("Connecting to heartbeat monitor %s", MONITOR_URI)
            async with connect(MONITOR_URI, ping_interval=None, open_timeout=3) as ws:
                welcome = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                if welcome != {"type": "ready"}:
                    raise RuntimeError("Monitor rejected heartbeat connection")
                logging.info("Heartbeat connection established (%s)", role)
                while True:
                    with vault.lock:
                        version = vault.state["version"]
                    await ws.send(json.dumps({
                        "type": "heartbeat", "role": role,
                        "sequence": sequence, "state_version": version
                    }))
                    logging.info("Heartbeat sent: role=%s sequence=%d", role, sequence)
                    sequence += 1
                    await asyncio.sleep(HEARTBEAT_INTERVAL)
        except Exception as exc:
            logging.warning("Heartbeat connection unavailable: %s; retrying", exc)
            await asyncio.sleep(2)


def fault_injector(probability, interval):
    while True:
        time.sleep(interval)
        if random.random() < probability:
            logging.error("[Simulated Crash] Terminating critical service")
            os._exit(1)


def run_service(host, port, role, require_existing=False,
                crash_probability=0.0, crash_interval=5.0):
    """Start service after checkpoint loading and acquiring the client port."""
    vault = VaultState(require_existing=require_existing)
    # The socket must be acquired before emitting any heartbeat.
    with VaultTCPServer((host, port), vault, role) as server:
        logging.info("%s listening on %s:%d; checkpoint v%d",
                     role.capitalize(), host, port, vault.state["version"])

        threading.Thread(
            target=lambda: asyncio.run(send_heartbeats(vault, role)),
            daemon=True, name="heartbeat-sender"
        ).start()
        threading.Thread(
            target=vault.periodic_checkpoints,
            args=(CHECKPOINT_INTERVAL,),
            daemon=True, name="checkpoint-writer"
        ).start()
        if crash_probability:
            threading.Thread(
                target=fault_injector,
                args=(crash_probability, crash_interval),
                daemon=True, name="fault-injector"
            ).start()

        try:
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            logging.info("Service interrupted")
        finally:
            server.shutdown()
