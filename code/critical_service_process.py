# Implement the core service as an independent running process. The process must
# experience unpredictable crashes during normal execution.
# Rule: Do not hard-code an arbitrary static conditional (e.g., if (request_count == 5)
# exit()). Implement stochastic or environmental crash triggers, such as:
#  A random probability trigger on incoming requests (e.g., simulating a memory
# leak or segfault on malformed input).
#  A randomized background fault-injection thread.
# The failure must terminate/exit the process completely (do not simply put the process to sleep). 


import asyncio
import json
import logging

from datetime import datetime, timezone
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

HEARTBEAT_MONITOR_URL = "ws://127.0.0.1:8000"

HEARTBEAT_INTERVAL = 5 # seconds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("critical_service.log"),
        logging.StreamHandler()
    ]
)


async def send_heartbeats():
    sequence = 1
    while True:
        try:
            async with connect(
                HEARTBEAT_MONITOR_URL,
                ping_interval=None
            ) as websocket:
                logging.info("Connected to heartbeat monitor.")

                while True:
                    heartbeat = {
                        "type": "heartbeat",
                        "sequence": sequence,
                        "timestamp": datetime.now(
                            timezone.utc
                        ).isoformat()
                    }
                    await websocket.send(json.dumps(heartbeat))
                    logging.info(f"Sent heartbeat #{sequence}")
                    sequence += 1
                    await asyncio.sleep(HEARTBEAT_INTERVAL)

        except (OSError, ConnectionClosed) as e:
            logging.warning(f"Monitor connection lost: {e}")
            logging.info("Reconnecting in 2 seconds...")
            await asyncio.sleep(2)


async def main():


    # Heartbeats run independently of service activity.
    await send_heartbeats()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.warning("Critical server stopped.")
