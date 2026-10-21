import socket
import threading
import json
import time
import sys
import queue
import threading
import sys
import time

# 1. Create a thread-safe queue for asynchronous terminal output
terminal_queue = queue.Queue()

def wopr_terminal_worker():
    """Consumes messages and prints them slowly without blocking the main network loop."""
    while True:
        text = terminal_queue.get()
        if text is None:  # Sentinel value to terminate
            break
        for char in str(text).upper():
            sys.stdout.write(char)
            sys.stdout.flush()
            time.sleep(0.03)  # The delay is now safely isolated to this worker thread
        print()
        terminal_queue.task_done()

# 2. Spawn the dedicated UI thread before starting the daemon
ui_thread = threading.Thread(target=wopr_terminal_worker, daemon=True)
ui_thread.start()

# 3. Replace the old type_text function so it instantly pushes to the queue instead of sleeping
def type_text(text, delay=None):
    terminal_queue.put(text)

# Mock cryptographic material
MOCK_PUBLIC_CERT = """-----BEGIN CERTIFICATE-----
MIIDXTCCAKWGAWIBAGIUA...[MOCKED FOR BREVITY]...
-----END CERTIFICATE-----"""

MOCK_SESSION_WRAPPER = {
    "CIPHERTEXT": "BASE64_ENCODED_CIPHERTEXT_HERE",
    "IV": "BASE64_ENCODED_IV",
    "MAC": "BASE64_ENCODED_MAC"
}

def type_text(text, delay=0.03):
    """Simulates the WOPR teletype output."""
    for char in str(text).upper():
        sys.stdout.write(char)
        sys.stdout.flush()
        time.sleep(delay)
    print()

class WOPR_Daemon:
    def __init__(self, host='127.0.0.1', port=9000):
        self.host = host
        self.port = port
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    def start(self):
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(5)
        type_text(f"GREETINGS. WOPR SYSTEM INITIALIZED.")
        type_text(f"AWAITING INSTRUCTIONS ON {self.host}:{self.port}...\n")
        
        try:
            while True:
                conn, addr = self.server_socket.accept()
                client_thread = threading.Thread(target=self._handle_client, args=(conn, addr))
                client_thread.daemon = True
                client_thread.start()
        except KeyboardInterrupt:
            print("\n")
            type_text("SYSTEM SHUTDOWN SEQUENCE INITIATED.")
        finally:
            self.server_socket.close()

    def _handle_client(self, conn, addr):
        is_heartbeat = False
        try:
            while True:
                raw_msglen = self._recvall(conn, 2)
                if not raw_msglen:
                    break
                
                msglen = int.from_bytes(raw_msglen, byteorder='big')
                data = self._recvall(conn, msglen)
                if not data:
                    break
                
                request = json.loads(data.decode('utf-8'))
                action = request.get('action', 'UNKNOWN').upper()
                
                if action == "HEARTBEAT":
                    is_heartbeat = True
                else:
                    # It is a real client; print the deferred connection logs
                    type_text(f"\n[!] INCOMING CONNECTION DETECTED FROM {addr}")
                    type_text(f"ANALYZING REQUEST: {action}...")
                
                response = self._process_request(request)
                self._send_msg(conn, response)
                
                if not is_heartbeat:
                    type_text("RESPONSE TRANSMITTED.")
                
        except json.JSONDecodeError:
            type_text(f"\n[!] ERROR: MALFORMED DATAGRAM RECEIVED FROM {addr}")
            self._send_msg(conn, {"status": "error", "message": "INVALID FORMAT"})
        except Exception as e:
            type_text(f"\n[!] FATAL ERROR ON LINK {addr}: {e}")
        finally:
            conn.close()
            # Only print termination if it wasn't a silent heartbeat ping
            if not is_heartbeat:
                type_text(f"CONNECTION TERMINATED FOR {addr}\n")

    def _process_request(self, request):
        action = request.get("action", "").upper()
        
        if action == "GET_PUBLIC_CERTIFICATE":
            return {
                "STATUS": "SUCCESS",
                "ACTION": action,
                "DATA": MOCK_PUBLIC_CERT
            }
        elif action == "GET_SESSION_WRAPPER":
            return {
                "STATUS": "SUCCESS",
                "ACTION": action,
                "DATA": MOCK_SESSION_WRAPPER
            }
        elif action == "HEARTBEAT":
            return {
                "STATUS": "ALIVE",
                "ACTION": action,
                "TIMESTAMP": time.time()
            }
        else:
            return {
                "STATUS": "ERROR",
                "MESSAGE": f"UNIDENTIFIED OPERATION CODE: {action}"
            }

    def _send_msg(self, sock, msg_dict):
        payload = json.dumps(msg_dict).encode('utf-8')
        header = len(payload).to_bytes(2, byteorder='big')
        sock.sendall(header + payload)

    def _recvall(self, sock, n):
        data = bytearray()
        while len(data) < n:
            packet = sock.recv(n - len(data))
            if not packet:
                return None
            data.extend(packet)
        return data

if __name__ == "__main__":
    daemon = WOPR_Daemon()
    daemon.start()