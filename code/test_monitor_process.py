import socket
import json
import time
import sys
import threading

def type_text(text, delay=0.01):
    """Simulates the WOPR teletype output."""
    for char in str(text).upper():
        sys.stdout.write(char)
        sys.stdout.flush()
        time.sleep(delay)
    print()

class HeartbeatMonitor:
    def __init__(self, host='127.0.0.1', port=9000, interval=5):
        self.host = host
        self.port = port
        self.interval = interval
        self.running = False
        self.monitor_thread = None

    def start(self):
        self.running = True
        type_text(f"INITIALIZING HEARTBEAT MONITOR THREAD... TARGET: {self.host}:{self.port}")
        # Spawn the monitor on its own thread
        self.monitor_thread = threading.Thread(target=self._monitor_loop)
        self.monitor_thread.daemon = True
        self.monitor_thread.start()

    def stop(self):
        self.running = False
        type_text("\nTERMINATING HEARTBEAT MONITOR THREAD...")

    def _monitor_loop(self):
        while self.running:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)  # Prevent hanging if daemon is unresponsive
            
            try:
                sock.connect((self.host, self.port))
                
                # Format request with 2-byte header schema
                request = {"action": "heartbeat"}
                payload = json.dumps(request).encode('utf-8')
                header = len(payload).to_bytes(2, byteorder='big')
                sock.sendall(header + payload)
                
                # Receive 2-byte length header
                raw_msglen = self._recvall(sock, 2)
                if raw_msglen:
                    msglen = int.from_bytes(raw_msglen, byteorder='big')
                    # Receive payload
                    data = self._recvall(sock, msglen)
                    
                    if data:
                        response = json.loads(data.decode('utf-8'))
                        if response.get("STATUS") == "ALIVE":
                            sys.stdout.write(f"\r[+] {time.strftime('%H:%M:%S')} - WOPR DAEMON IS RESPONSIVE. STATUS: ALIVE     ")
                            sys.stdout.flush()
                        else:
                            type_text(f"\n[!] WARNING: UNEXPECTED RESPONSE FROM DAEMON: {response}")
                    else:
                        type_text("\n[!] CRITICAL: DAEMON CONNECTED BUT DROPPED PAYLOAD.")
                else:
                    type_text("\n[!] CRITICAL: DAEMON CONNECTED BUT FAILED TO SEND HEADER.")
                    
            except ConnectionRefusedError:
                type_text(f"\n[!] ALERT: CONNECTION REFUSED. DAEMON AT {self.host}:{self.port} IS DOWN.")
            except socket.timeout:
                type_text(f"\n[!] ALERT: CONNECTION TIMEOUT. DAEMON AT {self.host}:{self.port} IS UNRESPONSIVE.")
            except Exception as e:
                type_text(f"\n[!] UNKNOWN ERROR DURING PING: {e}")
            finally:
                sock.close()
            
            # Wait for the next interval
            time.sleep(self.interval)

    def _recvall(self, sock, n):
        data = bytearray()
        while len(data) < n:
            try:
                packet = sock.recv(n - len(data))
                if not packet:
                    return None
                data.extend(packet)
            except socket.timeout:
                return None
        return data

if __name__ == "__main__":
    # The monitor polls every 3 seconds
    monitor = HeartbeatMonitor(interval=3)
    monitor.start()
    
    try:
        # Main thread stays alive while the background thread does the pinging
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        monitor.stop()