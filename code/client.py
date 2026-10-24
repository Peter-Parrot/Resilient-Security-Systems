import sys
import socket
import selectors
import types
import json
import time
import random
import logging

CLIENT_ID = "client_01"
REQUEST_TIMEOUT = 5.0

sel = selectors.DefaultSelector()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("client.log"),
        logging.StreamHandler()
    ]
)


def create_request(request_num, action, key_id=None, plaintext_hash=None):
    """Builds a request dictionary."""
    request = {
        "request_id": f"req-{request_num}",
        "action": action,
        "client_id": CLIENT_ID,
        "timestamp": time.time()
    }

    if key_id:
        request["key_id"] = key_id
    if plaintext_hash:
        request["plaintext_hash"] = plaintext_hash

    return request


def prompt_user_request(request_num):
    """Prompts the user for an action and any required fields."""
    while True:
        print("\n--- Available Actions: LIST_KEYS | GET_CERT | ENCRYPT_KEY | QUIT ---")
        action = input("Enter action: ").strip().upper()

        if action in ("QUIT", "EXIT"):
            return None

        if action == "LIST_KEYS":
            return create_request(request_num, action)

        if action == "GET_CERT":
            key_id = input("Enter key_id (e.g., key1, key2, key3): ").strip()
            return create_request(request_num, action, key_id=key_id)

        if action == "ENCRYPT_KEY":
            plaintext_hash = input(
                "Enter plaintext_hash (press Enter to auto-generate): "
            ).strip()
            if not plaintext_hash:
                plaintext_hash = f"session-{random.getrandbits(64):016x}"
            return create_request(request_num, action, plaintext_hash=plaintext_hash)

        print(
            f"Invalid action '{action}'. Please choose LIST_KEYS, GET_CERT, ENCRYPT_KEY, or QUIT.")


def queue_request(data, request):
    """Appends a newline-delimited JSON request to the connection's outbound buffer."""
    logging.info(
        "Sending request [%s]: action=%s",
        request["request_id"],
        request["action"]
    )
    msg = (json.dumps(request) + "\n").encode("utf-8")
    data.outb += msg
    data.waiting_for_response = True


def start_connection(host, port):
    """Initiates a persistent TCP connection and registers it with the selector."""
    server_addr = (host, port)
    logging.info("Connecting to server at %s...", server_addr)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        sock.settimeout(REQUEST_TIMEOUT)
        sock.connect(server_addr)
        sock.setblocking(False)
    except ConnectionRefusedError:
        logging.error(
            "Connection refused by %s. Critical service is offline or crashed.",
            server_addr
        )
        sock.close()
        return None
    except (socket.timeout, TimeoutError):
        logging.error("Connection to %s timed out.", server_addr)
        sock.close()
        return None
    except OSError as e:
        logging.error("Failed to connect to %s: %s", server_addr, e)
        sock.close()
        return None

    logging.info("Connected to %s.", server_addr)
    events = selectors.EVENT_READ | selectors.EVENT_WRITE
    data = types.SimpleNamespace(
        addr=server_addr,
        inb=b"",
        outb=b"",
        waiting_for_response=False
    )
    sel.register(sock, events, data=data)
    return data


def service_connection(key, mask):
    """Services read/write events on the persistent TCP connection."""
    sock = key.fileobj
    data = key.data

    if mask & selectors.EVENT_READ:
        try:
            recv_data = sock.recv(1024)
            if recv_data:
                data.inb += recv_data
            else:
                logging.error(
                    "Server %s closed connection unexpectedly (daemon crashed or stopped).",
                    data.addr
                )
                data.waiting_for_response = False
                sel.unregister(sock)
                sock.close()
                return
        except ConnectionRefusedError:
            logging.error(
                "Connection refused by %s. Critical service is offline or crashed.",
                data.addr
            )
            data.waiting_for_response = False
            sel.unregister(sock)
            sock.close()
            return
        except ConnectionResetError:
            logging.error(
                "Connection reset by %s. Critical service crashed!",
                data.addr
            )
            data.waiting_for_response = False
            sel.unregister(sock)
            sock.close()
            return
        except OSError as e:
            logging.error("Socket error with %s: %s", data.addr, e)
            data.waiting_for_response = False
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
                response = json.loads(line.decode("utf-8"))
                handle_response(response)
            except (json.JSONDecodeError, UnicodeDecodeError):
                logging.warning(
                    "Received malformed JSON response from server.")
            finally:
                data.waiting_for_response = False

    if mask & selectors.EVENT_WRITE:
        if data.outb:
            try:
                sent = sock.send(data.outb)
                data.outb = data.outb[sent:]
            except BlockingIOError:
                pass
            except (ConnectionRefusedError, ConnectionResetError, OSError) as e:
                logging.error("Failed to send data to %s: %s", data.addr, e)
                data.waiting_for_response = False
                sel.unregister(sock)
                sock.close()


def handle_response(response):
    """Logs the parsed JSON response from the key-storage daemon."""
    req_id = response.get("request_id", "unknown")
    status = response.get("status")

    if status == "OK":
        action = response.get("action")
        key_id = response.get("key_id", "N/A")
        logging.info(
            "Response [%s] STATUS=OK | action=%s | key_id=%s | data=%s",
            req_id, action, key_id, response.get("data")
        )
    else:
        error = response.get("error", "UNKNOWN_ERROR")
        message = response.get("message", "")
        logging.warning(
            "Response [%s] STATUS=ERROR | error=%s | message=%s",
            req_id, error, message
        )


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python client.py <host> <port>")
        sys.exit(1)

    try:
        host, port = sys.argv[1], int(sys.argv[2])
    except ValueError:
        logging.error(
            "Invalid port '%s'. Port must be an integer.", sys.argv[2])
        sys.exit(1)

    request_num = 1
    conn_data = start_connection(host, port)
    if conn_data is None:
        sys.exit(1)

    try:
        while sel.get_map():
            request = prompt_user_request(request_num)
            if request is None:
                logging.info("Closing client connection.")
                break

            queue_request(conn_data, request)
            request_num += 1
            request_start = time.monotonic()

            # Drive selector until the request is sent and the response arrives (or socket closes)
            try:
                while sel.get_map() and conn_data.waiting_for_response:
                    if time.monotonic() - request_start >= REQUEST_TIMEOUT:
                        raise TimeoutError(
                            f"Request {request['request_id']} timed out after {REQUEST_TIMEOUT}s."
                        )
                    events = sel.select(timeout=0.5)
                    for key, mask in events:
                        service_connection(key, mask)
            except TimeoutError as e:
                logging.error("%s", e)
                conn_data.waiting_for_response = False

    except (KeyboardInterrupt, EOFError):
        logging.info("Caught interrupt, stopping client.")
    finally:
        for key in list(sel.get_map().values()):
            sel.unregister(key.fileobj)
            key.fileobj.close()
        sel.close()
