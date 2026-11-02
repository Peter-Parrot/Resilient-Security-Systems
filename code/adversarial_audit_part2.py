
"""
Modes:
  checkpoint: corrupt an isolated checkpoint and try to activate the backup.

  split-brain: pause a locally launched primary (SIGSTOP, Linux/macOS)
      so its heartbeats and TCP health checks stop while its port
      remains bound. Observe whether the standby incorrectly becomes
      a second active writer.

The audit creates throwaway state and cleans up only processes it launches.
"""

import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time


PROJECT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORTS = (5000, 5001, 8000)


def assert_unused_ports():
    for port in PORTS:
        with socket.socket() as s:
            if s.connect_ex((HOST, port)) == 0:
                raise RuntimeError(
                    f"Port {port} is already in use. "
                    "Stop existing project processes first."
                )


def launch(script, args, folder, env, handles):
    logfile = open(
        folder / f"{script}.log",
        "w",
        encoding="utf-8"
    )
    handles.append(logfile)

    proc = subprocess.Popen(
        [sys.executable, str(PROJECT / script), *args],
        cwd=folder,
        env=env,
        stdout=logfile,
        stderr=subprocess.STDOUT
    )

    handles.append(proc)
    return proc


def request(port=5000):
    with socket.create_connection((HOST, port), timeout=1) as s:
        s.settimeout(1)

        s.sendall(
            b'{"request_id":"audit","action":"GET_STATE",'
            b'"client_id":"audit"}\n'
        )

        data = b""

        while b"\n" not in data:
            piece = s.recv(4096)

            if not piece:
                raise RuntimeError(
                    "Connection closed without response"
                )

            data += piece

    return json.loads(data.split(b"\n", 1)[0])


def cleanup(handles):
    for obj in handles:
        if isinstance(obj, subprocess.Popen):
            if obj.poll() is None:
                if hasattr(signal, "SIGCONT"):
                    try:
                        os.kill(obj.pid, signal.SIGCONT)
                    except ProcessLookupError:
                        pass

                obj.terminate()

                try:
                    obj.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    obj.kill()
                    obj.wait()
        else:
            obj.close()


def checkpoint_audit(folder, env):
    # Build a legitimate, disposable checkpoint
    # using the actual application code.
    from vault_state import VaultState

    state_path = Path(env["KEY_SERVICE_STATE_FILE"])
    vault = VaultState(path=state_path)

    original = vault.handle_request({
        "request_id": "r1",
        "client_id": "audit",
        "action": "ENCRYPT_KEY",
        "plaintext_hash": "test"
    })

    assert original["status"] == "OK"

    checkpoint = json.loads(state_path.read_text())

    print(
        f"Created legitimate checkpoint: "
        f"version={checkpoint['version']}"
    )

    # Change a counter so it disagrees with the state.
    # Avoid altering real files.
    checkpoint["version"] += 100

    state_path.write_text(
        json.dumps(checkpoint),
        encoding="utf-8"
    )

    print(
        "Attack: corrupted checkpoint version "
        "without updating encryption_count"
    )

    # Launch against a free local port,
    # and disable monitoring assumptions.
    with socket.socket() as s:
        s.bind((HOST, 0))
        test_port = s.getsockname()[1]

    proc = subprocess.run(
        [
            sys.executable,
            str(PROJECT / "backup_service_process.py"),
            HOST,
            str(test_port),
            "--activate"
        ],
        cwd=folder,
        env=env,
        capture_output=True,
        text=True,
        timeout=8
    )

    print(f"Backup exit code: {proc.returncode}")

    print(
        "Backup output:\n"
        + (proc.stdout + proc.stderr)[-1400:]
    )

    passed = (
        proc.returncode != 0
        and "checkpoint" in (
            proc.stdout + proc.stderr
        ).lower()
    )

    print(
        "RESULT:",
        "PASS — backup rejected inconsistent state"
        if passed
        else "INCONCLUSIVE — inspect logs"
    )

    return passed


def split_brain_audit(folder, env):
    if not hasattr(signal, "SIGSTOP"):
        raise RuntimeError(
            "This scenario requires SIGSTOP (Linux/macOS)."
        )

    assert_unused_ports()
    handles = []

    try:
        monitor = launch(
            "heartbeat_monitor_process.py",
            [],
            folder,
            env,
            handles
        )

        standby = launch(
            "backup_service_process.py",
            ["--standby"],
            folder,
            env,
            handles
        )

        primary = launch(
            "critical_service_process.py",
            ["--crash-probability", "0"],
            folder,
            env,
            handles
        )

        deadline = time.monotonic() + 8

        while time.monotonic() < deadline:
            if any(
                p.poll() is not None
                for p in (monitor, standby, primary)
            ):
                raise RuntimeError(
                    "A process exited during startup; "
                    "inspect logs in " + str(folder)
                )

            try:
                if request().get("server_role") == "primary":
                    break
            except (OSError, ValueError, RuntimeError):
                pass

            time.sleep(0.2)

        else:
            raise RuntimeError(
                "Primary did not become healthy; inspect logs"
            )

        # Do not pause until the monitor has observed
        # an actual heartbeat.
        heartbeat_deadline = time.monotonic() + 8

        while time.monotonic() < heartbeat_deadline:
            monitor_log = (
                folder / "heartbeat_monitor_process.py.log"
            ).read_text()

            if "Heartbeat received:" in monitor_log:
                break

            time.sleep(0.2)

        else:
            raise RuntimeError(
                "Monitor never received first heartbeat; inspect logs"
            )

        print(
            "Primary is alive and serving on port 5000; "
            "monitor saw a heartbeat"
        )

        os.kill(primary.pid, signal.SIGSTOP)

        print(
            "Attack: SIGSTOP paused primary "
            "without releasing port 5000"
        )

        print(
            "Waiting ~19 seconds for the 15-second "
            "heartbeat timeout and failover..."
        )

        time.sleep(19)

        print("Backup still running:", standby.poll() is None)

        monitor_text = (
            folder / "heartbeat_monitor_process.py.log"
        ).read_text()

        backup_text = (
            folder / "backup_service_process.py.log"
        ).read_text()

        print(
            "Crash detected:",
            "[Crash Detected]" in monitor_text
        )

        print(
            "Promotion attempted:",
            "[Failover Triggered]" in monitor_text
        )

        print(
            "Backup activation rejected:",
            "Backup activation failed" in backup_text
        )

        os.kill(primary.pid, signal.SIGCONT)
        time.sleep(0.5)

        state = request()

        print(
            "Service owner after resuming primary:",
            state.get("server_role")
        )

        passed = (
            "[Crash Detected]" in monitor_text
            and "[Failover Triggered]" in monitor_text
            and state.get("server_role") == "primary"
            and "[Service Restored]" not in monitor_text
        )

        print(
            "RESULT:",
            "PASS — no second active service observed"
            if passed
            else "INCONCLUSIVE — inspect logs"
        )

        return passed

    finally:
        cleanup(handles)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "mode",
        choices=["checkpoint", "split-brain"]
    )

    args = parser.parse_args()

    required = [
        "vault_state.py",
        "service_runtime.py",
        "backup_service_process.py",
        "critical_service_process.py",
        "heartbeat_monitor_process.py"
    ]

    missing = [
        name for name in required
        if not (PROJECT / name).exists()
    ]

    if missing:
        parser.error(
            "Place script in corrected project folder; missing: "
            + ", ".join(missing)
        )

    with tempfile.TemporaryDirectory(
        prefix="cs456-audit-"
    ) as tmp:

        folder = Path(tmp)

        env = os.environ.copy()

        env["KEY_SERVICE_STATE_FILE"] = str(
            folder / "vault.json"
        )

        print(
            "Isolated test state:",
            env["KEY_SERVICE_STATE_FILE"]
        )

        if args.mode == "checkpoint":
            success = checkpoint_audit(folder, env)
        else:
            success = split_brain_audit(folder, env)

        if not success:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
