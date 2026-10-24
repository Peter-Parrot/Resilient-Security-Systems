import argparse
from dataclasses import dataclass
from datetime import datetime
import math
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText


SCRIPTS = {
    "Monitor": "heartbeat_monitor_process.py",
    "Service": "critical_service_process.py",
    "Client": "client.py",
    "Attack": "adversarial_audit_script.py",
}
POLL_MS = 100
LOG_LINE_LIMIT = 2000


def positive_number(value):
    """Reject NaN, infinity, zero, and negative timing values."""
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(
            "Timing values must be finite numbers greater than zero.")
    return number


def monitor_message(line):
    """Extract the message from this project's timestamp/level log format."""
    parts = line.rstrip().split(" - ", 2)
    if len(parts) == 3 and parts[1] in {"INFO", "WARNING", "ERROR", "CRITICAL"}:
        return parts[2]
    return ""


@dataclass
class Child:
    process: subprocess.Popen | None = None
    stop_deadline: float | None = None
    exit_reported: bool = False

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None


def collect_output(name, process, events):
    """Only the Tk main thread may edit widgets; readers just enqueue text."""
    try:
        with process.stdout:
            for line in process.stdout:
                events.put((name, process, line))
    except (OSError, ValueError) as error:
        events.put((name, process, f"Output reader stopped: {error}\n"))


class DemoApp:
    def __init__(self, root, project_root):
        self.root = root
        self.project_root = project_root
        self.children = {name: Child() for name in SCRIPTS}
        self.events = queue.Queue()
        self.closing = False
        self.monitor_ready = False
        self.monitor_connected = False
        self.reported_health = "UNKNOWN"
        self.timeout_seconds = None
        self.heartbeat_count = 0
        self.last_heartbeat_at = None
        self.service_stopped_at = None
        self.service_has_run = False
        self.attack_active = False
        self.logs = {}
        self.states = {}
        self.buttons = {}
        self.client_action = tk.StringVar(value="LIST_KEYS")
        self.client_key_id = tk.StringVar(value="key1")
        self.client_plaintext = tk.StringVar(value="")
        self.send_client_button = None
        self.mode = tk.StringVar(value="false-liveness")
        self.interval = tk.StringVar(value="2")
        self.duration = tk.StringVar(value="30")
        self.health_text = tk.StringVar(value="UNKNOWN")
        self.beat_text = tk.StringVar(value="No heartbeats received")
        self.observation = tk.StringVar(
            value="Start the monitor, then the service.")
        self.build_window()
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.tick()

    def build_window(self):
        self.root.title("Mini Project One")
        self.root.geometry("980x840")
        self.root.minsize(780, 700)
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Heartbeat Security", font=(
            "TkDefaultFont", 20, "bold")).pack(anchor="w")
        ttk.Label(outer, text=f"Project: {self.project_root}", wraplength=900).pack(
            anchor="w", pady=(4, 12))

        controls = ttk.LabelFrame(
            outer, text="Processes launched by this window", padding=10)
        controls.pack(fill="x")
        controls.columnconfigure(3, weight=1)
        for row, name in enumerate(SCRIPTS):
            ttk.Label(controls, text=name, width=12).grid(
                row=row, column=0, sticky="w", pady=4)
            start = ttk.Button(controls, text="Start",
                               command=lambda n=name: self.start(n))
            start.grid(row=row, column=1, padx=4)
            stop = ttk.Button(controls, text="Stop",
                              command=lambda n=name: self.stop(n))
            stop.grid(row=row, column=2, padx=4)
            state = tk.StringVar(value="Not started")
            ttk.Label(controls, textvariable=state).grid(
                row=row, column=3, sticky="w", padx=12)
            self.states[name] = state
            self.buttons[name] = (start, stop)

        client_frame = ttk.LabelFrame(
            outer, text="Client Requests", padding=10)
        client_frame.pack(fill="x", pady=(10, 0))
        ttk.Label(client_frame, text="Action").grid(row=0, column=0, padx=4)
        ttk.Combobox(
            client_frame,
            textvariable=self.client_action,
            values=("LIST_KEYS", "GET_CERT", "ENCRYPT_KEY"),
            state="readonly",
            width=14,
        ).grid(row=0, column=1, padx=4)
        ttk.Label(client_frame, text="Key ID").grid(row=0, column=2, padx=4)
        ttk.Combobox(
            client_frame,
            textvariable=self.client_key_id,
            values=("key1", "key2", "key3"),
            width=10,
        ).grid(row=0, column=3, padx=4)
        ttk.Label(client_frame, text="Plaintext Hash").grid(
            row=0, column=4, padx=4)
        ttk.Entry(client_frame, textvariable=self.client_plaintext,
                  width=22).grid(row=0, column=5, padx=4)
        self.send_client_button = ttk.Button(
            client_frame, text="Send Request", command=self.send_client_request
        )
        self.send_client_button.grid(row=0, column=6, padx=8)
        ttk.Label(
            client_frame,
            text="Start the service, then start the client to open a persistent TCP session. Use 'Send Request' to query the vault.",
            wraplength=850,
        ).grid(row=1, column=0, columnspan=7, sticky="w", pady=(8, 0))

        attack = ttk.LabelFrame(outer, text="Attack settings", padding=10)
        attack.pack(fill="x", pady=10)
        ttk.Label(attack, text="Mode").grid(row=0, column=0, padx=4)
        ttk.Combobox(attack, textvariable=self.mode, values=(
            "spoof", "replay", "false-liveness"), state="readonly", width=18).grid(row=0, column=1, padx=4)
        ttk.Label(attack, text="Interval (s)").grid(row=0, column=2, padx=4)
        ttk.Entry(attack, textvariable=self.interval,
                  width=8).grid(row=0, column=3, padx=4)
        ttk.Label(attack, text="Duration (s)").grid(row=0, column=4, padx=4)
        ttk.Entry(attack, textvariable=self.duration,
                  width=8).grid(row=0, column=5, padx=4)
        ttk.Label(attack, text="Start the service, then arm the attack. It takes over automatically after the service crashes; duration starts at takeover.",
                  wraplength=850).grid(row=1, column=0, columnspan=6, sticky="w", pady=(8, 0))

        health = ttk.LabelFrame(
            outer, text="What the monitor reports", padding=10)
        health.pack(fill="x")
        ttk.Label(health, textvariable=self.health_text, font=(
            "TkDefaultFont", 16, "bold")).pack(anchor="w")
        ttk.Label(health, textvariable=self.beat_text).pack(anchor="w", pady=4)
        ttk.Label(health, textvariable=self.observation,
                  wraplength=900).pack(anchor="w")

        tabs = ttk.Notebook(outer)
        tabs.pack(fill="both", expand=True, pady=10)
        for name in SCRIPTS:
            panel = ttk.Frame(tabs)
            tabs.add(panel, text=name)
            log = ScrolledText(panel, height=12, wrap="word",
                               state="disabled", font=("TkFixedFont", 11))
            log.pack(fill="both", expand=True)
            self.logs[name] = log

        footer = ttk.Frame(outer)
        footer.pack(fill="x")
        ttk.Button(footer, text="Stop All",
                   command=self.stop_all).pack(side="left")
        ttk.Button(footer, text="Clear Displayed Logs",
                   command=self.clear_logs).pack(side="left", padx=8)
        ttk.Label(
            footer, text="Only GUI-launched processes are tracked.").pack(side="right")

    def append_log(self, name, text):
        widget = self.logs[name]
        widget.configure(state="normal")
        widget.insert("end", text)
        lines = int(widget.index("end-1c").split(".")[0])
        if lines > LOG_LINE_LIMIT:
            widget.delete("1.0", f"{lines - LOG_LINE_LIMIT + 1}.0")
        widget.see("end")
        widget.configure(state="disabled")

    def note(self, name, text):
        self.append_log(name, f"[GUI {datetime.now():%H:%M:%S}] {text}\n")

    def clear_logs(self):
        for widget in self.logs.values():
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            widget.configure(state="disabled")

    def send_client_request(self):
        client = self.children["Client"]
        if not client.running or client.process is None or client.process.stdin is None:
            messagebox.showinfo("Client not running",
                                "Start the Client process first.")
            return

        action = self.client_action.get().strip().upper()
        lines = [action]
        if action == "GET_CERT":
            lines.append(self.client_key_id.get().strip())
        elif action == "ENCRYPT_KEY":
            lines.append(self.client_plaintext.get().strip())

        payload = "\n".join(lines) + "\n"
        try:
            client.process.stdin.write(payload)
            client.process.stdin.flush()
            self.note(
                "Client", f"Dispatched {action} request to client stdin.")
        except (BrokenPipeError, OSError) as error:
            self.note("Client", f"Failed to write to client stdin: {error}")

    def start(self, name):
        if self.closing or self.children[name].running:
            return
        if name == "Client" and not self.children["Service"].running:
            messagebox.showinfo(
                "Service not running", "Start the Service before starting the Client.")
            return
        if name not in ("Monitor", "Client") and not self.monitor_ready:
            messagebox.showinfo(
                "Monitor not ready", "Start the monitor and wait until it is listening.")
            return
        if name == "Service" and (self.children["Attack"].running or self.monitor_connected):
            messagebox.showinfo(
                "Monitor busy", "Stop the attack and wait for the monitor to log its disconnect.")
            return
        arguments = []
        if name in ("Service", "Client"):
            arguments = ["127.0.0.1", "5000"]
        if name == "Attack":
            try:
                interval = positive_number(self.interval.get())
                duration = positive_number(self.duration.get())
            except ValueError:
                messagebox.showerror(
                    "Invalid timing", "Interval and duration must be finite numbers greater than zero.")
                return
            arguments = [self.mode.get(), "--interval", str(interval), "--duration",
                         str(duration), "--log", str(self.project_root / "heartbeat.log")]

        script = self.project_root / "code" / SCRIPTS[name]
        if not script.is_file():
            messagebox.showerror("Missing script", f"Cannot find:\n{script}")
            return
        stdin_mode = subprocess.PIPE if name == "Client" else subprocess.DEVNULL
        try:
            process = subprocess.Popen(
                [sys.executable, "-u", str(script), *arguments],
                cwd=self.project_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=stdin_mode,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as error:
            messagebox.showerror("Could not start process", str(error))
            return

        self.children[name] = Child(process=process)
        if name == "Monitor":
            self.monitor_ready = False
            self.monitor_connected = False
            self.reported_health = "UNKNOWN"
            self.timeout_seconds = None
            self.heartbeat_count = 0
            self.last_heartbeat_at = None
        elif name == "Service":
            self.service_has_run = True
            self.service_stopped_at = None
        elif name == "Attack":
            self.attack_active = False
        self.note(name, f"Started PID {process.pid}.")
        threading.Thread(target=collect_output, args=(
            name, process, self.events), daemon=True).start()
        self.update_display()

    def stop(self, name):
        child = self.children[name]
        if not child.running or child.stop_deadline is not None:
            return
        child.stop_deadline = time.monotonic() + 2
        self.note(
            name, "Stop requested; this is a manual stop, not an injected random crash.")
        if name == "Monitor":
            self.monitor_ready = False
            self.reported_health = "UNKNOWN"
        if name == "Client" and child.process is not None and child.process.stdin is not None:
            try:
                child.process.stdin.write("QUIT\n")
                child.process.stdin.flush()
            except OSError:
                pass
        try:
            child.process.terminate()
        except ProcessLookupError:
            pass
        except OSError as error:
            self.note(name, f"Could not terminate: {error}")

    def stop_all(self):
        for name in ("Client", "Attack", "Service", "Monitor"):
            self.stop(name)

    def close(self):
        self.closing = True
        self.stop_all()

    def read_monitor_event(self, line):
        event = monitor_message(line)
        if event.startswith("server listening on "):
            # websockets emits this after binding; the project's earlier
            # 'Heartbeat monitor listening' message occurs before binding.
            self.monitor_ready = True
        match = re.fullmatch(
            r"Timeout threshold: (\d+(?:\.\d+)?) seconds", event)
        if match:
            self.timeout_seconds = float(match.group(1))
        if event == "Critical server connected.":
            self.monitor_connected = True
        elif event == "Critical server disconnected.":
            self.monitor_connected = False
        elif event == "Critical server is ONLINE.":
            self.reported_health = "ONLINE"
        elif event.startswith("ALERT: Critical server is DOWN!"):
            self.reported_health = "DOWN"
        elif event.startswith("Heartbeat received: "):
            self.heartbeat_count += 1
            self.last_heartbeat_at = time.monotonic()

    def update_display(self):
        monitor = self.children["Monitor"]
        monitor_active = monitor.running and monitor.stop_deadline is None
        available = monitor_active and self.monitor_ready
        service = self.children["Service"]
        service_active = service.running and service.stop_deadline is None
        for name, child in self.children.items():
            running = child.running
            if running:
                state = f"Running (PID {child.process.pid})"
                if child.stop_deadline is not None:
                    state = "Stopping..."
                elif name == "Monitor" and not self.monitor_ready:
                    state = "Starting; waiting for listener..."
                elif name == "Attack":
                    state = "Active; sending fake heartbeats" if self.attack_active else "Armed; waiting to take over"
            elif child.process is None:
                state = "Not started"
            else:
                state = f"Stopped (exit {child.process.returncode})"
            self.states[name].set(state)
            can_start = not running and not self.closing
            if name == "Client":
                can_start = can_start and service_active
            elif name != "Monitor":
                can_start = can_start and available
                if name == "Service":
                    can_start = can_start and not self.monitor_connected and not self.children[
                        "Attack"].running
            self.buttons[name][0].configure(
                state="normal" if can_start else "disabled")
            self.buttons[name][1].configure(
                state="normal" if running and child.stop_deadline is None and not self.closing else "disabled")

        if self.send_client_button is not None:
            client_child = self.children["Client"]
            client_ready = client_child.running and client_child.stop_deadline is None and not self.closing
            self.send_client_button.configure(
                state="normal" if client_ready else "disabled")

        health = self.reported_health if monitor_active else "UNKNOWN (monitor stopped)"
        self.health_text.set(health)
        if self.last_heartbeat_at is None:
            self.beat_text.set(
                "No heartbeats received in this monitor session")
        else:
            age = time.monotonic() - self.last_heartbeat_at
            suffix = " (monitor stopped)" if not monitor_active else ""
            self.beat_text.set(
                f"Accepted heartbeats: {self.heartbeat_count} | Last observed {age:.1f}s ago{suffix}")

        if self.closing:
            observation = "Stopping child processes and closing..."
        elif not monitor_active:
            observation = "Start the monitor, then the service. Separate terminal sessions are not tracked."
        elif self.children["Service"].running:
            observation = "Service is running. An armed attack will take over after it disconnects." if self.children[
                "Attack"].running else "Service is running. Start the attack now to arm automatic takeover."
        elif self.reported_health == "ONLINE" and self.service_stopped_at is not None:
            elapsed = time.monotonic() - self.service_stopped_at
            timeout = self.timeout_seconds
            if timeout is None or elapsed < timeout + 0.5:
                observation = "Service stopped; ONLINE can be normal while the monitor's timeout window expires."
            elif self.last_heartbeat_at is not None and self.last_heartbeat_at > self.service_stopped_at:
                observation = "Possible false liveness: the GUI-launched service is stopped, but accepted heartbeats keep the monitor ONLINE beyond its timeout."
            else:
                observation = "Service stopped; monitor still reports ONLINE. Check the monitor log for delayed or missing alerts."
        elif self.reported_health == "DOWN":
            observation = "The monitor reports DOWN. Start an attack to test whether fake heartbeats make it report ONLINE."
        elif not self.service_has_run:
            observation = "Start the service to establish a baseline. Processes started outside this window are not tracked."
        else:
            observation = "Service stopped. Start an attack after the monitor logs its disconnect."
        self.observation.set(observation)

    def tick(self):
        # Bound work per tick so a burst of logs cannot freeze the window.
        for _ in range(300):
            try:
                name, process, line = self.events.get_nowait()
            except queue.Empty:
                break
            if self.children[name].process is not process:
                continue  # Ignore delayed output from a previous run.
            self.append_log(name, line)
            child = self.children[name]
            if name == "Monitor" and child.running and child.stop_deadline is None:
                self.read_monitor_event(line)
            elif name == "Attack":
                if line.startswith(("Attack active;", "Attack reconnected.")):
                    self.attack_active = True

        for name, child in self.children.items():
            if child.process is None:
                continue
            if child.running:
                if child.stop_deadline is not None and time.monotonic() >= child.stop_deadline:
                    try:
                        child.process.kill()
                        self.note(
                            name, "Process did not stop in time; sent kill.")
                    except ProcessLookupError:
                        pass
                    except OSError as error:
                        self.note(name, f"Could not kill: {error}")
                    child.stop_deadline = time.monotonic() + 2
            elif not child.exit_reported:
                child.exit_reported = True
                self.note(
                    name, f"Exited with code {child.process.returncode}.")
                if name == "Monitor":
                    self.monitor_ready = False
                    self.monitor_connected = False
                    self.reported_health = "UNKNOWN"
                elif name == "Service":
                    self.service_stopped_at = time.monotonic()

        self.update_display()
        if self.closing and not any(child.running for child in self.children.values()):
            self.root.destroy()
            return
        self.root.after(POLL_MS, self.tick)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve(
    ).parents[1], help="Repository root; defaults to the parent of code/.")
    args = parser.parse_args()
    project_root = args.project_root.expanduser().resolve()
    missing = [str(project_root / "code" / filename) for filename in SCRIPTS.values()
               if not (project_root / "code" / filename).is_file()]
    if missing:
        parser.error(
            "Missing project scripts; copy this file into code/ or pass --project-root.\n" + "\n".join(missing))
    try:
        root = tk.Tk()
    except tk.TclError as error:
        raise SystemExit(
            f"Cannot open Tkinter: {error}. Check with: python -m tkinter") from error
    DemoApp(root, project_root)
    root.mainloop()


if __name__ == "__main__":
    main()
