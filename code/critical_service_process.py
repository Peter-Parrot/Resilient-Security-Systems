import argparse
import math
import socket
import selectors
import types
import json
import time
import random
import os
import threading

import asyncio
import logging
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

URI = "ws://127.0.0.1:8000"
HEARTBEAT_INTERVAL = 5

sel = selectors.DefaultSelector()

logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
        logging.FileHandler("key_storage_daemon.log"),
        logging.StreamHandler()
    ]
)

KEYS = {
    "key1": {
        "algorithm": "RSA-2048",
        "fingerprint_sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
        "payload": "-----BEGIN CERTIFICATE-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAkey1...\n-----END CERTIFICATE-----"
    },
    "key2": {
        "algorithm": "RSA-2048",
        "fingerprint_sha256": "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8",
        "payload": "-----BEGIN CERTIFICATE-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAkey2...\n-----END CERTIFICATE-----"
    },
    "key3": {
        "algorithm": "ECDSA-P256",
        "fingerprint_sha256": "a665a45920422f9d417e4867efdc4fb8a04a1f3fff1fa07e998e86f7f7a27ae3",
        "payload": "-----BEGIN CERTIFICATE-----\nMIIBHgJBANB9...ECDSA...==\n-----END CERTIFICATE-----"
    }
}


def handle_request(request_data):
    """Processes incoming request dictionary and returns response dictionary."""
    request_id = request_data.get("request_id")
    action = request_data.get("action")
    key_id = request_data.get("key_id")
    
    response = {
        "request_id": request_id,
        "timestamp": time.time()
    }
    
    if not action:
        response.update({
            "status": "ERROR",
            "error": "INVALID_REQUEST",
            "message": "Missing 'action' field in request."
        })
        return response
        
    response["action"] = action
    if key_id is not None:
        response["key_id"] = key_id

    if action == "GET_CERT":
        if not key_id:
            response.update({
                "status": "ERROR",
                "error": "MISSING_KEY_ID",
                "message": "Action 'GET_CERT' requires 'key_id'."
            })
        elif key_id in KEYS:
            response.update({
                "status": "OK",
                "data": KEYS[key_id]
            })
        else:
            response.update({
                "status": "ERROR",
                "error": "KEY_NOT_FOUND",
                "message": f"Key ID '{key_id}' does not exist in vault."
            })
            
    elif action == "ENCRYPT_KEY":
        plaintext_hash = request_data.get("plaintext_hash", "default-dummy-hash")
        # Simulate key encryption output
        encrypted_payload = f"ENCRYPTED_BLOB::{plaintext_hash}::SECURE"
        response.update({
            "status": "OK",
            "data": {
                "algorithm": "AES-256-GCM",
                "fingerprint_sha256": "cf23df2207d99a74fbe169e3eba035e633b65d947b344046b7fcb0484821a508",
                "payload": encrypted_payload
            }
        })
        
    elif action == "LIST_KEYS":
        keys_list = list(KEYS.keys())
        response.update({
            "status": "OK",
            "data": {
                "algorithm": "INDEX",
                "fingerprint_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                "payload": json.dumps(keys_list)
            }
        })
    else:
        response.update({
            "status": "ERROR",
            "error": "UNKNOWN_ACTION",
            "message": f"Unsupported action '{action}'."
        })
        
    return response

def accept_wrapper(sock):
    conn, addr = sock.accept()
    print(f"Accepted connection from {addr}")
    conn.setblocking(False)
    data = types.SimpleNamespace(
        addr=addr, 
        inb=b"", 
        outb=b"")
    events = selectors.EVENT_READ | selectors.EVENT_WRITE
    sel.register(conn, events, data=data)

def service_connection(key, mask):
    sock = key.fileobj
    data = key.data
    if mask & selectors.EVENT_READ:
        try:
            recv_data = sock.recv(1024)
            if recv_data:
                data.inb += recv_data
            else:
                print(f"Closing connection to {data.addr}")
                sel.unregister(sock)
                sock.close()
                return
        except ConnectionResetError:
            print(f"Connection reset by {data.addr}")
            sel.unregister(sock)
            sock.close()
            return

        # Process newline-delimited JSON framing
        while b"\n" in data.inb:
            line, data.inb = data.inb.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                req_obj = json.loads(line.decode("utf-8"))
                res_obj = handle_request(req_obj)
                resp_line = json.dumps(res_obj) + "\n"
                data.outb += resp_line.encode("utf-8")
            except json.JSONDecodeError:
                err_res = {
                    "request_id": "unknown",
                    "status": "ERROR",
                    "error": "MALFORMED_JSON",
                    "message": "Failed to decode JSON request.",
                    "timestamp": time.time()
                }
                data.outb += (json.dumps(err_res) + "\n").encode("utf-8")

    if mask & selectors.EVENT_WRITE:
        if data.outb:
            try:
                sent = sock.send(data.outb)
                data.outb = data.outb[sent:]
            except BlockingIOError:
                pass


async def send_heartbeats():
    # Outer loop ensures the client attempts to reconnect if disconnected
    while True:
        try:
            logging.info("Connecting to monitor at %s...", URI)
            
            async with connect(URI) as websocket:
                logging.info("Connected. Starting heartbeat transmission.")
                
                # Inner loop handles the continuous payload delivery
                while True:
                    stochastic_crash()
                    payload = json.dumps({"type": "heartbeat"})
                    await websocket.send(payload)
                    logging.info("Heartbeat sent.")
                    
                    await asyncio.sleep(HEARTBEAT_INTERVAL)
                    
        except ConnectionClosed:
            logging.warning("Connection closed by server. Retrying in 5s...")
            await asyncio.sleep(5)
        except OSError:
            logging.error("Monitor is unreachable (connection refused). Retrying in 5s...")
            await asyncio.sleep(5)
        except Exception as e:
            logging.error("Unexpected error: %s", e)
            await asyncio.sleep(5)


def start_heartbeat_thread():
    asyncio.run(send_heartbeats()) 

def stochastic_crash():
    if random.random() < 0.1:
        print("Simulated System Crash, Server is Down")
        os._exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Key service with heartbeat and random failure injection.")
    parser.add_argument("host", nargs="?", default="127.0.0.1")
    parser.add_argument("port", nargs="?", type=int, default=5000)
    parser.add_argument("--crash-probability", type=float, default=0.1,
                        help="Probability per background check; 0 disables faults (default: 0.1).")
    parser.add_argument("--crash-interval", type=float, default=5.0,
                        help="Seconds between random fault checks (default: 5).")
    args = parser.parse_args()
    if not math.isfinite(args.crash_probability) or not 0 <= args.crash_probability <= 1:
        parser.error("Crash probability must be between 0 and 1.")
    if not math.isfinite(args.crash_interval) or args.crash_interval <= 0:
        parser.error("Crash interval must be finite and positive.")
    host, port = args.host, args.port
    lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    lsock.bind((host, port))
    lsock.listen()
    print(f"Listening on {(host, port)}")
    lsock.setblocking(False)
    sel.register(lsock, selectors.EVENT_READ, data=None)
    
    async_heartbeat_thread = threading.Thread(target=start_heartbeat_thread, daemon=True)
    async_heartbeat_thread.start()
    if args.crash_probability > 0:
        logging.info("Random fault checks every %s seconds, probability %s.",
                     args.crash_interval, args.crash_probability)
        threading.Thread(target=stochastic_crash,
                         args=(args.crash_probability, args.crash_interval), daemon=True).start()


    try:
        while True:
            events = sel.select(timeout=None)
            for key, mask in events:
                if key.data is None:
                    accept_wrapper(key.fileobj)
                else:
                    service_connection(key, mask)
    except KeyboardInterrupt:
        print("Caught keyboard interrupt, exiting")
    finally:
        sel.close()
