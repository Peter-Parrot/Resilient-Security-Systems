"""Run with: python -m unittest discover -s tests -v"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

CODE = Path(__file__).resolve().parents[1] / "code"
sys.path.insert(0, str(CODE))
from vault_state import VaultState


def unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def query(port, request):
    with socket.create_connection(("127.0.0.1", port), timeout=1) as sock:
        sock.sendall((json.dumps(request) + "\n").encode())
        return json.loads(sock.makefile("rb").readline())


def wait_ready(port, process):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"Service exited: {process.returncode}")
        try:
            result = query(port, {"action": "GET_STATE"})
            if result.get("status") == "OK":
                return result
        except (OSError, ValueError):
            pass
        time.sleep(0.05)
    raise AssertionError("Service did not become ready")


class StateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "vault.json"
        self.vault = VaultState(self.path)
        self.request = {"action": "ENCRYPT_KEY", "client_id": "test-client",
                        "request_id": "req-1", "plaintext_hash": "before-crash"}

    def test_restore_and_retry_without_double_counting(self):
        result = self.vault.handle_request(self.request)
        restored = VaultState(self.path, require_existing=True)
        retry = restored.handle_request(self.request)
        self.assertEqual(result["key_id"], retry["key_id"])
        self.assertEqual(result["data"], retry["data"])
        state = restored.handle_request({"action": "GET_STATE"})["data"]
        self.assertEqual(state, {"version": 1, "encryption_count": 1, "key_count": 4})
        listed = restored.handle_request({"action": "LIST_KEYS"})
        self.assertIn(result["key_id"], json.loads(listed["data"]["payload"]))
        cert = restored.handle_request({"action": "GET_CERT", "key_id": result["key_id"]})
        self.assertEqual(cert["data"], result["data"])

    def test_failed_checkpoint_rolls_back(self):
        with self.assertLogs(level="ERROR"), patch.object(self.vault, "checkpoint", side_effect=OSError("disk full")):
            self.assertEqual(self.vault.handle_request(self.request)["error"], "CHECKPOINT_FAILED")
        self.assertEqual(self.vault.state["version"], 0)
        self.assertEqual(VaultState(self.path).state["version"], 0)

    def test_interrupted_replace_preserves_checkpoint(self):
        with self.assertLogs(level="ERROR"), patch("vault_state.os.replace", side_effect=OSError("interrupted")):
            self.assertEqual(self.vault.handle_request(self.request)["status"], "ERROR")
        self.assertEqual(VaultState(self.path).state["version"], 0)
        self.assertEqual(list(self.path.parent.glob(".vault-*")), [])

    def test_invalid_requests_and_conflicting_retry(self):
        for request in ([], None, {"action": "GET_CERT", "key_id": []},
                        {"action": "ENCRYPT_KEY"}):
            self.assertEqual(self.vault.handle_request(request)["status"], "ERROR")
        self.vault.handle_request(self.request)
        changed = dict(self.request, plaintext_hash="different")
        self.assertEqual(self.vault.handle_request(changed)["error"], "REQUEST_ID_CONFLICT")

    def test_missing_or_corrupt_checkpoint_refused(self):
        with self.assertRaises(ValueError):
            VaultState(self.path.parent / "missing.json", require_existing=True)
        for content in ('{broken', '{"schema_version": 999}',
                        json.dumps(dict(self.vault.state, encryption_count=5))):
            self.path.write_text(content)
            with self.assertRaises(ValueError):
                VaultState(self.path, require_existing=True)


class ProcessRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cwd = Path(self.directory.name)
        self.env = dict(os.environ, KEY_SERVICE_STATE_FILE=str(self.cwd / "vault.json"),
                        PYTHONPATH=str(CODE))
        self.log = (self.cwd / "processes.log").open("w+")
        self.addCleanup(self.log.close)
        self.processes = []
        self.addCleanup(self.stop_processes)
        self.port = unused_port()
        self.control = unused_port()

    def launch(self, *args):
        process = subprocess.Popen([sys.executable, *map(str, args)], cwd=self.cwd,
                                   env=self.env, stdout=self.log, stderr=self.log)
        self.processes.append(process)
        return process

    def stop_processes(self):
        for process in reversed(self.processes):
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def exercise_recovery(self, warm):
        primary = self.launch(CODE / "critical_service_process.py", "127.0.0.1", self.port,
                              "--crash-probability", 0)
        wait_ready(self.port, primary)
        if warm:
            backup = self.launch(CODE / "backup_service_process.py", "127.0.0.1", self.port,
                                 "--standby", "--control-port", self.control)
            # Wait for standby readiness without sending a promotion yet.
            deadline = time.monotonic() + 5
            while True:
                try:
                    with socket.create_connection(("127.0.0.1", self.control), timeout=0.2):
                        break
                except OSError:
                    if time.monotonic() > deadline:
                        self.fail("Standby control listener did not start")
                    time.sleep(0.05)
        request = {"action": "ENCRYPT_KEY", "client_id": "integration", "request_id": "one",
                   "plaintext_hash": "pre-crash-operation"}
        first = query(self.port, request)
        second = query(self.port, dict(request, request_id="two", plaintext_hash="another"))
        expected = query(self.port, {"action": "GET_STATE"})["data"]
        primary.kill()  # No shutdown handler can save state here.
        primary.wait(timeout=3)
        if warm:
            with socket.create_connection(("127.0.0.1", self.control), timeout=1) as sock:
                sock.sendall(b'{"action":"PROMOTE"}\n')
                self.assertIn(b"PROMOTED", sock.recv(1024))
        else:
            backup = self.launch(CODE / "backup_service_process.py", "127.0.0.1", self.port,
                                 "--control-port", self.control)
        self.assertEqual(wait_ready(self.port, backup)["data"], expected)
        for result in (first, second):
            retrieved = query(self.port, {"action": "GET_CERT", "key_id": result["key_id"]})
            self.assertEqual(retrieved["data"], result["data"])
        retry = query(self.port, request)
        self.assertEqual(retry["key_id"], first["key_id"])
        self.assertEqual(query(self.port, {"action": "GET_STATE"})["data"], expected)
        # The recovered node also persists new operations across a second restart.
        query(self.port, dict(request, request_id="three"))
        backup.kill()
        backup.wait(timeout=3)
        recovered = self.launch(CODE / "backup_service_process.py", "127.0.0.1", self.port,
                                "--control-port", self.control)
        self.assertEqual(wait_ready(self.port, recovered)["data"]["encryption_count"], 3)

    def test_cold_backup_after_abrupt_crash(self):
        self.exercise_recovery(warm=False)

    def test_warm_backup_loads_at_promotion(self):
        self.exercise_recovery(warm=True)

    def test_random_crash_runs_without_monitor(self):
        process = self.launch(CODE / "critical_service_process.py", "127.0.0.1", self.port,
                              "--crash-probability", 1, "--crash-interval", 0.2)
        self.assertEqual(process.wait(timeout=5), 1)
        self.log.flush()
        self.assertIn("Injected stochastic crash", (self.cwd / "processes.log").read_text())


if __name__ == "__main__":
    unittest.main()
