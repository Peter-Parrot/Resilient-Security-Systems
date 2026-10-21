import socket
import json
import time
import sys

def type_text(text, delay=0.03):
    """Simulates the WOPR teletype output."""
    for char in str(text).upper():
        sys.stdout.write(char)
        sys.stdout.flush()
        time.sleep(delay)
    print()

class NORAD_Client:
    def __init__(self, host='127.0.0.1', port=9000):
        self.host = host
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    def connect(self):
        type_text(f"ESTABLISHING SECURE UPLINK TO {self.host}:{self.port}...")
        self.sock.connect((self.host, self.port))
        type_text("UPLINK ESTABLISHED.\n")

    def request_material(self, action):
        request = {"action": action}
        self._send_msg(request)
        return self._recv_msg()

    def _send_msg(self, msg_dict):
        payload = json.dumps(msg_dict).encode('utf-8')
        header = len(payload).to_bytes(2, byteorder='big')
        self.sock.sendall(header + payload)

    def _recv_msg(self):
        raw_msglen = self._recvall(2)
        if not raw_msglen:
            return None
        msglen = int.from_bytes(raw_msglen, byteorder='big')
        data = self._recvall(msglen)
        return json.loads(data.decode('utf-8'))

    def _recvall(self, n):
        data = bytearray()
        while len(data) < n:
            packet = self.sock.recv(n - len(data))
            if not packet:
                return None
            data.extend(packet)
        return data

    def close(self):
        self.sock.close()
        type_text("\nCONNECTION SEVERED.")

if __name__ == "__main__":
    time.sleep(1)
    type_text("GREETINGS PROFESSOR FALKEN.", delay=0.06)
    time.sleep(1)
    type_text("SHALL WE PLAY A GAME?\n", delay=0.06)
    time.sleep(0.5)

    client = NORAD_Client()
    client.connect()

    type_text("--- DISPATCHING REQUEST: PUBLIC CERTIFICATE ---")
    cert_response = client.request_material("get_public_certificate")
    # Speed up the printing for data payloads so you don't wait forever
    type_text(json.dumps(cert_response, indent=2), delay=0.005)

    print("\n")
    time.sleep(1)

    type_text("--- DISPATCHING REQUEST: ENCRYPTED SESSION WRAPPER ---")
    wrapper_response = client.request_material("get_session_wrapper")
    type_text(json.dumps(wrapper_response, indent=2), delay=0.005)

    client.close()