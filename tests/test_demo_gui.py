"""Headless checks for GUI configuration, commands, prompts, and recovery status."""
import io
from pathlib import Path
import queue
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from demo_gui import Child, DemoApp, SCRIPTS, collect_output, monitor_settings


def value(text):
    return SimpleNamespace(get=lambda: text)


class DemoGuiTests(unittest.TestCase):
    def app(self):
        app = DemoApp.__new__(DemoApp)
        app.settings = {"SERVICE_HOST": "127.0.0.1", "SERVICE_PORT": 5050,
                        "CONTROL_PORT": 5001, "PORT": 8000}
        app.probability, app.crash_interval = value("0.3"), value("3")
        app.audit_mode = value("split-brain")
        app.monitor_ready = app.monitor_connected = False
        app.reported_health, app.active_role = "UNKNOWN", None
        app.heartbeat_count = 0
        app.last_heartbeat_at = None
        return app

    def test_reads_monitor_ports_without_executing_script(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "code").mkdir()
            (root / "code" / SCRIPTS["Monitor"]).write_text(
                'raise RuntimeError("must not execute")\nSERVICE_PORT = 5050\nCONTROL_PORT = 5011\n')
            settings = monitor_settings(root)
            self.assertEqual(settings["SERVICE_PORT"], 5050)
            self.assertEqual(settings["CONTROL_PORT"], 5011)

    def test_process_commands_match_current_ports_and_fault_settings(self):
        app = self.app()
        self.assertEqual(app.start_arguments("Service"), ["127.0.0.1", "5050",
                         "--crash-probability", "0.3", "--crash-interval", "3.0"])
        self.assertEqual(app.start_arguments("Backup"), ["127.0.0.1", "5050", "--standby", "--control-port", "5001"])
        self.assertEqual(app.start_arguments("Client"), ["127.0.0.1", "5050"])
        self.assertEqual(app.start_arguments("Audit"), ["split-brain"])
        for probability in ("nan", "-1", "2"):
            app.probability = value(probability)
            with self.assertRaises(ValueError):
                app.start_arguments("Service")

    def test_monitor_events_follow_crash_and_promotion(self):
        app = self.app()
        def emit(message):
            app.read_monitor_event("2026-11-02 12:00:00 - INFO - " + message)
        emit("Heartbeat monitor listening on 127.0.0.1:8000; timeout 15.0s")
        self.assertFalse(app.monitor_ready)  # This line occurs before the bind.
        self.assertEqual(app.timeout_seconds, 15)
        emit("server listening on 127.0.0.1:8000")
        self.assertTrue(app.monitor_ready)
        emit("Heartbeat sender connected")
        emit("Critical service is ONLINE (role=primary)")
        self.assertTrue(app.monitor_connected)
        self.assertEqual(app.active_role, "primary")
        emit("Heartbeat received: {'type': 'heartbeat'}")
        self.assertEqual(app.heartbeat_count, 1)
        emit("[Crash Detected] No heartbeat")
        self.assertEqual(app.reported_health, "DOWN")
        emit("[Failover Triggered] Starting recovery")
        self.assertEqual(app.reported_health, "RECOVERING")
        emit("[Service Restored] Backup active; state_version=2")
        self.assertEqual((app.reported_health, app.active_role), ("ONLINE", "backup"))

    def test_client_prompt_is_forwarded_without_newline(self):
        events = queue.Queue()
        process = SimpleNamespace(stdout=io.StringIO("Enter action: "))
        collect_output("Client", process, events)
        self.assertEqual(events.get_nowait(), ("Client", process, "Enter action: "))

    def test_fresh_demo_resets_exited_processes(self):
        app = self.app()
        app.children = {name: Child(process=SimpleNamespace(poll=lambda: 0)) for name in SCRIPTS}
        app.start = Mock(return_value=True)
        app.start_demo()
        app.start.assert_called_once_with("Monitor")
        self.assertTrue(app.starting_demo)
        self.assertTrue(all(child.process is None for child in app.children.values()))

    def test_audit_cannot_start_while_demo_is_running(self):
        app = self.app()
        app.closing = False
        app.children = {name: Child() for name in SCRIPTS}
        app.children["Monitor"] = Child(process=SimpleNamespace(poll=lambda: None))
        with patch("demo_gui.messagebox.showinfo") as message:
            self.assertFalse(app.start("Audit"))
            message.assert_called_once()


class GuiCommandRecoveryTests(unittest.TestCase):
    """Exercise the GUI's command builder against an isolated real cluster."""

    def test_generated_commands_preserve_state_across_random_crash(self):
        import json
        import os
        import shutil
        import socket
        import subprocess
        import time

        code = Path(__file__).resolve().parents[1] / "code"
        processes = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "code").mkdir()
            for source in code.glob("*.py"):
                shutil.copy2(source, root / "code" / source.name)
            ports = []
            reservations = []
            try:
                for _ in range(3):
                    sock = socket.socket()
                    sock.bind(("127.0.0.1", 0))
                    reservations.append(sock)
                    ports.append(sock.getsockname()[1])
            finally:
                for sock in reservations:
                    sock.close()
            service_port, control_port, monitor_port = ports
            monitor = root / "code" / SCRIPTS["Monitor"]
            content = monitor.read_text()
            content = content.replace("SERVICE_PORT = 5050", f"SERVICE_PORT = {service_port}")
            content = content.replace("CONTROL_PORT = 5001", f"CONTROL_PORT = {control_port}")
            content = content.replace("PORT = 8000", f"PORT = {monitor_port}")
            content = content.replace("TIMEOUT = HEARTBEAT_INTERVAL * 3", "TIMEOUT = 3.0")
            monitor.write_text(content)
            runtime = root / "code" / "service_runtime.py"
            runtime.write_text(runtime.read_text().replace("127.0.0.1:8000", f"127.0.0.1:{monitor_port}"))
            app = DemoApp.__new__(DemoApp)
            app.settings = monitor_settings(root)
            app.probability, app.crash_interval = value("1"), value("2")
            env = dict(os.environ, KEY_SERVICE_STATE_FILE=str(root / "vault.json"))
            log = (root / "test.log").open("w")
            try:
                for name in ("Monitor", "Backup", "Service"):
                    process = subprocess.Popen(
                        [sys.executable, "-u", str(root / "code" / SCRIPTS[name]), *app.start_arguments(name)],
                        cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT)
                    processes.append(process)
                    if name in ("Monitor", "Backup"):
                        marker = "server listening on " if name == "Monitor" else "WARM STANDBY"
                        ready_deadline = time.monotonic() + 4
                        while marker not in (root / "test.log").read_text():
                            if process.poll() is not None or time.monotonic() > ready_deadline:
                                self.fail((root / "test.log").read_text())
                            time.sleep(0.02)
                def request(payload):
                    with socket.create_connection(("127.0.0.1", service_port), timeout=0.5) as sock:
                        sock.sendall((json.dumps(payload) + "\n").encode())
                        return json.loads(sock.makefile("rb").readline())
                deadline = time.monotonic() + 1.5
                while True:
                    try:
                        created = request({"action": "ENCRYPT_KEY", "client_id": "gui-check",
                                           "request_id": "one", "plaintext_hash": "before-crash"})
                        break
                    except OSError:
                        if time.monotonic() > deadline:
                            self.fail((root / "test.log").read_text())
                        time.sleep(0.02)
                self.assertEqual(created["status"], "OK")
                self.assertEqual(processes[-1].wait(timeout=4), 1)
                deadline = time.monotonic() + 6
                while True:
                    try:
                        state = request({"action": "GET_STATE"})
                        if state["server_role"] == "backup":
                            break
                    except OSError:
                        pass
                    if time.monotonic() > deadline:
                        self.fail((root / "test.log").read_text())
                    time.sleep(0.05)
                self.assertEqual(state["data"]["encryption_count"], 1)
                retained = request({"action": "GET_CERT", "key_id": created["key_id"]})
                self.assertEqual(retained["data"], created["data"])
                client = subprocess.run(
                    [sys.executable, "-u", str(root / "code" / SCRIPTS["Client"]), *app.start_arguments("Client")],
                    input="GET_STATE\nQUIT\n", text=True, cwd=root, env=env,
                    capture_output=True, timeout=5)
                self.assertEqual(client.returncode, 0, client.stderr)
                self.assertIn("STATUS=OK", client.stderr)
            finally:
                for process in reversed(processes):
                    if process.poll() is None:
                        process.terminate()
                    process.wait(timeout=3)
                log.close()


if __name__ == "__main__":
    unittest.main()
