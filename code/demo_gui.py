"""Side-by-side dashboard for passive failover, client requests, and audits."""
import argparse
import ast
from dataclasses import dataclass
from datetime import datetime
import math
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

SCRIPTS = {
    "Monitor": "heartbeat_monitor_process.py",
    "Backup": "backup_service_process.py",
    "Service": "critical_service_process.py",
    "Client": "client.py",
    "Audit": "adversarial_audit_part2.py",
    "Attack": "adversarial_audit_script.py",
}
LABELS = {"Service": "Primary", "Backup": "Standby", "Attack": "Heartbeat attack"}
POLL_MS = 100
LOG_LINE_LIMIT = 2000


def positive_number(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("Timing values must be finite and greater than zero.")
    return number


def monitor_message(line):
    parts = line.rstrip().split(" - ", 2)
    return parts[2] if len(parts) == 3 and parts[1] in {"INFO", "WARNING", "ERROR", "CRITICAL"} else ""


def monitor_settings(project_root):
    """Read literal settings without importing the monitor or opening its log."""
    settings = {"SERVICE_HOST": "127.0.0.1", "SERVICE_PORT": 5000,
                "CONTROL_PORT": 5001, "PORT": 8000}
    tree = ast.parse((project_root / "code" / SCRIPTS["Monitor"]).read_text())
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name) and target.id in settings:
                    settings[target.id] = ast.literal_eval(statement.value)
    return settings


@dataclass
class Child:
    process: subprocess.Popen | None = None
    stop_deadline: float | None = None
    exit_reported: bool = False

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None


def collect_output(name, process, events):
    # Client input prompts do not end in newlines, so stream characters rather
    # than waiting for readline() to finish a prompt.
    try:
        with process.stdout:
            buffer = ""
            while True:
                char = process.stdout.read(1)
                if not char:
                    if buffer:
                        events.put((name, process, buffer))
                    break
                buffer += char
                if char == "\n" or (name == "Client" and buffer.endswith(": ")):
                    events.put((name, process, buffer))
                    buffer = ""
    except (OSError, ValueError) as exc:
        events.put((name, process, f"Output reader stopped: {exc}\n"))


class DemoApp:
    def __init__(self, root, project_root):
        self.root, self.project_root = root, project_root
        self.settings = monitor_settings(project_root)
        self.children = {name: Child() for name in SCRIPTS}
        self.events = queue.Queue()
        self.closing = False
        self.starting_demo = False
        self.monitor_ready = self.monitor_connected = False
        self.backup_ready = self.service_ready = False
        self.client_ready = False
        self.active_role = None
        self.reported_health = "UNKNOWN"
        self.timeout_seconds = 15.0
        self.heartbeat_count = 0
        self.last_heartbeat_at = None
        self.service_stopped_at = None
        self.attack_active = False
        self.logs, self.states, self.buttons = {}, {}, {}
        self.client_action = tk.StringVar(value="GET_STATE")
        self.client_key_id = tk.StringVar(value="key1")
        self.client_plaintext = tk.StringVar(value="")
        self.probability = tk.StringVar(value="0.3")
        self.crash_interval = tk.StringVar(value="3")
        self.audit_mode = tk.StringVar(value="checkpoint")
        self.mode = tk.StringVar(value="false-liveness")
        self.interval = tk.StringVar(value="2")
        self.duration = tk.StringVar(value="30")
        self.health_text = tk.StringVar(value="Waiting to start")
        self.beat_text = tk.StringVar(value="No heartbeats received")
        self.observation = tk.StringVar(value="Start Demo opens the monitor, standby, primary, then client.")
        self.build_window()
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.tick()

    def build_window(self):
        self.root.title("Passive Redundancy Demo")
        width = min(1440, self.root.winfo_screenwidth() - 60)
        height = min(980, self.root.winfo_screenheight() - 100)
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(1050, 700)
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)
        title = ttk.Frame(outer)
        title.pack(fill="x")
        ttk.Label(title, text="Passive Redundancy", font=("TkDefaultFont", 19, "bold")).pack(side="left")
        self.start_demo_button = ttk.Button(title, text="Start Demo", command=self.start_demo)
        self.start_demo_button.pack(side="right", padx=4)
        ttk.Button(title, text="Stop All", command=self.stop_all).pack(side="right", padx=4)
        ttk.Button(title, text="Clear Logs", command=self.clear_logs).pack(side="right", padx=4)
        ttk.Label(outer, text=f"Service {self.settings['SERVICE_HOST']}:{self.settings['SERVICE_PORT']}  ·  "
                  f"Standby control {self.settings['CONTROL_PORT']}  ·  Heartbeats {self.settings['PORT']}").pack(anchor="w", pady=(3, 8))

        controls = ttk.Frame(outer)
        controls.pack(fill="x")
        for column, name in enumerate(("Monitor", "Backup", "Service", "Client")):
            controls.columnconfigure(column, weight=1)
            card = ttk.LabelFrame(controls, text=LABELS.get(name, name), padding=6)
            card.grid(row=0, column=column, sticky="nsew", padx=3)
            self.process_controls(card, name)
        fault = ttk.Frame(outer)
        fault.pack(fill="x", pady=6)
        ttk.Label(fault, text="Primary crash probability").pack(side="left")
        ttk.Entry(fault, textvariable=self.probability, width=6).pack(side="left", padx=5)
        ttk.Label(fault, text="Check every (seconds)").pack(side="left")
        ttk.Entry(fault, textvariable=self.crash_interval, width=6).pack(side="left", padx=5)
        ttk.Label(fault, text="0 disables random crashes; settings apply on the next primary start.").pack(side="left", padx=8)

        health = ttk.Frame(outer)
        health.pack(fill="x", pady=(0, 6))
        ttk.Label(health, textvariable=self.health_text, font=("TkDefaultFont", 13, "bold")).pack(side="left")
        ttk.Label(health, textvariable=self.beat_text).pack(side="right")
        ttk.Label(outer, textvariable=self.observation, wraplength=1200).pack(anchor="w", pady=(0, 6))

        split = ttk.Panedwindow(outer, orient="vertical")
        split.pack(fill="both", expand=True)
        core = ttk.Frame(split)
        split.add(core, weight=3)
        for column, name in enumerate(("Service", "Monitor", "Backup")):
            core.columnconfigure(column, weight=1, uniform="logs")
            core.rowconfigure(0, weight=1)
            panel = ttk.LabelFrame(core, text=LABELS.get(name, name), padding=4)
            panel.grid(row=0, column=column, sticky="nsew", padx=3)
            self.log_panel(panel, name)

        bottom = ttk.Frame(split)
        split.add(bottom, weight=2)
        bottom.columnconfigure(0, weight=1, uniform="bottom")
        bottom.columnconfigure(1, weight=1, uniform="bottom")
        bottom.rowconfigure(0, weight=1)
        client = ttk.LabelFrame(bottom, text="Client requests and responses", padding=6)
        client.grid(row=0, column=0, sticky="nsew", padx=3, pady=(6, 0))
        row = ttk.Frame(client)
        row.pack(fill="x")
        ttk.Combobox(row, textvariable=self.client_action,
                     values=("GET_STATE", "LIST_KEYS", "GET_CERT", "ENCRYPT_KEY"),
                     state="readonly", width=14).pack(side="left")
        self.send_client_button = ttk.Button(row, text="Send", command=self.send_client_request)
        self.send_client_button.pack(side="left", padx=6)
        fields = ttk.Frame(client)
        fields.pack(fill="x", pady=4)
        fields.columnconfigure(1, weight=1)
        ttk.Label(fields, text="Key ID").grid(row=0, column=0, sticky="w")
        ttk.Entry(fields, textvariable=self.client_key_id).grid(row=0, column=1, sticky="ew", padx=5)
        ttk.Label(fields, text="Plaintext hash").grid(row=1, column=0, sticky="w")
        ttk.Entry(fields, textvariable=self.client_plaintext).grid(row=1, column=1, sticky="ew", padx=5)
        self.log_panel(client, "Client")

        tabs = ttk.Notebook(bottom)
        tabs.grid(row=0, column=1, sticky="nsew", padx=3, pady=(6, 0))
        audit = ttk.Frame(tabs, padding=6)
        tabs.add(audit, text="Part 2 audits")
        audit_controls = ttk.Frame(audit)
        audit_controls.pack(fill="x")
        ttk.Combobox(audit_controls, textvariable=self.audit_mode,
                     values=("checkpoint", "split-brain"), state="readonly", width=15).pack(side="left")
        self.process_controls(audit_controls, "Audit")
        ttk.Label(audit, text="Stop the demo first. Each audit launches its own isolated test processes.",
                  wraplength=580).pack(anchor="w", pady=4)
        self.log_panel(audit, "Audit")
        attack = ttk.Frame(tabs, padding=6)
        tabs.add(attack, text="Part 1 heartbeat attack")
        row = ttk.Frame(attack)
        row.pack(fill="x")
        ttk.Combobox(row, textvariable=self.mode, values=("spoof", "replay", "false-liveness"),
                     state="readonly", width=15).pack(side="left")
        self.process_controls(row, "Attack")
        timing = ttk.Frame(attack)
        timing.pack(fill="x", pady=4)
        for label, variable in (("Interval (s)", self.interval), ("Duration (s)", self.duration)):
            ttk.Label(timing, text=label).pack(side="left")
            ttk.Entry(timing, textvariable=variable, width=6).pack(side="left", padx=5)
        self.log_panel(attack, "Attack")

    def process_controls(self, parent, name):
        start = ttk.Button(parent, text="Run" if name == "Audit" else "Start", width=7,
                           command=lambda: self.start(name))
        stop = ttk.Button(parent, text="Stop", width=6, command=lambda: self.stop(name))
        start.pack(side="left", padx=2)
        stop.pack(side="left", padx=2)
        self.states[name] = tk.StringVar(value="Not started")
        ttk.Label(parent, textvariable=self.states[name]).pack(side="left", padx=4)
        self.buttons[name] = start, stop

    def log_panel(self, parent, name):
        log = ScrolledText(parent, height=7, width=30, wrap="word", state="disabled", font=("TkFixedFont", 10))
        log.pack(fill="both", expand=True)
        log.tag_configure("error", foreground="#b42318")
        log.tag_configure("success", foreground="#067647")
        log.tag_configure("event", foreground="#175cd3")
        self.logs[name] = log

    def append_log(self, name, text):
        tag = ""
        if "[Service Restored]" in text or "PASS" in text or "STATUS=OK" in text:
            tag = "success"
        elif any(word in text for word in ("ERROR", "CRITICAL", "Traceback", "[Simulated Crash]")):
            tag = "error"
        elif any(word in text for word in ("[Failover Triggered]", "Promotion", "WARM STANDBY")):
            tag = "event"
        widget = self.logs[name]
        widget.configure(state="normal")
        widget.insert("end", text, tag)
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

    def start_demo(self):
        if any(child.running for child in self.children.values()):
            messagebox.showinfo("Processes running", "Stop All and wait for the processes to exit before starting a fresh demo.")
            return
        self.children = {name: Child() for name in SCRIPTS}
        self.starting_demo = True
        self.backup_ready = self.service_ready = False
        if not self.start("Monitor"):
            self.starting_demo = False

    def start_arguments(self, name):
        target = [self.settings["SERVICE_HOST"], str(self.settings["SERVICE_PORT"])]
        if name == "Service":
            probability = float(self.probability.get())
            if not math.isfinite(probability) or not 0 <= probability <= 1:
                raise ValueError("Crash probability must be between zero and one.")
            interval = positive_number(self.crash_interval.get())
            return target + ["--crash-probability", str(probability), "--crash-interval", str(interval)]
        if name == "Backup":
            return target + ["--standby", "--control-port", str(self.settings["CONTROL_PORT"])]
        if name == "Client":
            return target
        if name == "Audit":
            return [self.audit_mode.get()]
        if name == "Attack":
            return [self.mode.get(), "--interval", str(positive_number(self.interval.get())),
                    "--duration", str(positive_number(self.duration.get())),
                    "--log", str(self.project_root / "heartbeat.log")]
        return []

    def start(self, name):
        if self.closing or self.children[name].running:
            return False
        if name == "Audit":
            if any(child.running for key, child in self.children.items() if key != "Audit"):
                messagebox.showinfo("Stop demo first", "The audit launches its own processes on the same ports. Click Stop All and wait before running it.")
                return False
        elif self.children["Audit"].running:
            messagebox.showinfo("Audit running", "Wait for the audit to finish or stop it before starting the demo.")
            return False
        if name == "Client" and not (self.service_ready or self.active_role == "backup"):
            messagebox.showinfo("Service not ready", "Wait for the primary or promoted backup to start listening.")
            return False
        if name in ("Service", "Backup", "Attack") and not self.monitor_ready:
            messagebox.showinfo("Monitor not ready", "Start the monitor and wait for its listener.")
            return False
        if name == "Service" and (self.monitor_connected or self.children["Attack"].running):
            messagebox.showinfo("Service already active", "Stop All before starting another primary after failover or an attack.")
            return False
        try:
            arguments = self.start_arguments(name)
        except ValueError as exc:
            messagebox.showerror("Invalid settings", str(exc))
            return False
        script = self.project_root / "code" / SCRIPTS[name]
        try:
            process = subprocess.Popen(
                [sys.executable, "-u", str(script), *arguments], cwd=self.project_root,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE if name == "Client" else subprocess.DEVNULL,
                start_new_session=(os.name == "posix"),
                text=True, encoding="utf-8", errors="replace")
        except OSError as exc:
            messagebox.showerror("Could not start process", str(exc))
            return False
        self.children[name] = Child(process=process)
        if name == "Monitor":
            self.monitor_ready = self.monitor_connected = False
            self.reported_health, self.active_role = "UNKNOWN", None
            self.heartbeat_count, self.last_heartbeat_at = 0, None
        elif name == "Backup":
            self.backup_ready = False
        elif name == "Service":
            self.service_ready, self.service_stopped_at = False, None
        elif name == "Client":
            self.client_ready = False
        elif name == "Attack":
            self.attack_active = False
        self.note(name, f"Started PID {process.pid}.")
        threading.Thread(target=collect_output, args=(name, process, self.events), daemon=True).start()
        return True

    def signal_child(self, child, sig):
        if child.process is None:
            return
        try:
            if os.name == "posix":
                # Each launch owns a fresh process group. Include audit-created
                # children and resume SIGSTOP targets so they can terminate.
                os.killpg(child.process.pid, signal.SIGCONT)
                os.killpg(child.process.pid, sig)
            elif child.running:
                child.process.kill() if sig == signal.SIGKILL else child.process.terminate()
        except ProcessLookupError:
            pass
        except OSError as exc:
            self.note("Monitor", f"Could not stop process group: {exc}")

    def stop(self, name):
        child = self.children[name]
        if child.process is None or child.stop_deadline is not None:
            return
        if child.running:
            child.stop_deadline = time.monotonic() + 2
            self.note(name, "Manual stop requested.")
        self.signal_child(child, signal.SIGTERM)
        if name == "Monitor":
            self.monitor_ready = False
        elif name == "Client":
            self.client_ready = False

    def stop_all(self):
        self.starting_demo = False
        self.service_ready = self.backup_ready = self.client_ready = False
        self.active_role = None
        for name in ("Client", "Attack", "Audit", "Service", "Backup", "Monitor"):
            self.stop(name)

    def close(self):
        self.closing = True
        self.stop_all()

    def send_client_request(self):
        child = self.children["Client"]
        if not child.running or not self.client_ready:
            return
        action = self.client_action.get()
        lines = [action]
        if action == "GET_CERT":
            lines.append(self.client_key_id.get().strip())
        elif action == "ENCRYPT_KEY":
            lines.append(self.client_plaintext.get().strip())
        try:
            child.process.stdin.write("\n".join(lines) + "\n")
            child.process.stdin.flush()
            self.client_ready = False
            self.note("Client", f"Sent {action}.")
        except (OSError, ValueError) as exc:
            self.note("Client", f"Cannot send request: {exc}")

    def read_monitor_event(self, line):
        event = monitor_message(line)
        if event.startswith("server listening on "):
            self.monitor_ready = True
        timeout = re.search(r"(?:timeout |Timeout threshold: )(\d+(?:\.\d+)?)(?:s| seconds)$", event)
        if timeout:
            self.timeout_seconds = float(timeout.group(1))
        if event in ("Heartbeat sender connected", "Critical server connected."):
            self.monitor_connected = True
        elif event in ("Heartbeat sender disconnected", "Critical server disconnected."):
            self.monitor_connected = False
        elif event.startswith("Critical service is ONLINE") or event == "Critical server is ONLINE.":
            self.reported_health = "ONLINE"
            self.active_role = "backup" if "role=backup" in event else "primary"
        elif event.startswith(("[Crash Detected]", "ALERT: Critical server is DOWN!")):
            self.reported_health = "DOWN"
            self.active_role = None
        elif event.startswith("[Failover Triggered]"):
            self.reported_health = "RECOVERING"
        elif event.startswith("[Service Restored]"):
            self.reported_health, self.active_role = "ONLINE", "backup"
        elif event.startswith("Heartbeat received:"):
            self.heartbeat_count += 1
            self.last_heartbeat_at = time.monotonic()

    def update_display(self):
        monitor_active = self.children["Monitor"].running and self.monitor_ready
        audit_running = self.children["Audit"].running
        for name, child in self.children.items():
            state = "Not started" if child.process is None else f"Exited ({child.process.returncode})"
            if child.running:
                state = f"Running · {child.process.pid}"
                if child.stop_deadline:
                    state = "Stopping…"
                elif name == "Backup":
                    state = "Serving" if self.active_role == "backup" else "Waiting for promotion"
                elif name == "Attack":
                    state = "Sending heartbeats" if self.attack_active else "Armed"
            self.states[name].set(state)
            allowed = not child.running and not self.closing and not audit_running
            if name == "Audit":
                allowed = not self.closing and not any(c.running for c in self.children.values())
            elif name == "Client":
                allowed = allowed and (self.service_ready or self.active_role == "backup")
            elif name != "Monitor":
                allowed = allowed and monitor_active
                if name == "Service":
                    allowed = allowed and not self.monitor_connected and not self.children["Attack"].running
            self.buttons[name][0].configure(state="normal" if allowed else "disabled")
            self.buttons[name][1].configure(state="normal" if child.running and not child.stop_deadline else "disabled")
        idle = not any(child.running for child in self.children.values())
        self.start_demo_button.configure(state="normal" if idle and not self.closing else "disabled")
        self.send_client_button.configure(state="normal" if self.children["Client"].running and self.client_ready and not self.closing else "disabled")
        role = "Standby serving" if self.active_role == "backup" else "Primary serving"
        self.health_text.set(f"{self.reported_health} · {role}" if monitor_active and self.reported_health == "ONLINE"
                             else self.reported_health if monitor_active else "Monitor stopped")
        if self.last_heartbeat_at is not None:
            self.beat_text.set(f"{self.heartbeat_count} heartbeats · Last received {time.monotonic() - self.last_heartbeat_at:.1f}s ago")
        else:
            self.beat_text.set("No heartbeats received")
        if self.closing:
            text = "Stopping processes and closing…"
        elif audit_running:
            text = "Audit running in its own test environment. Follow its results in the audit panel."
        elif self.active_role == "backup":
            text = "The standby has taken over. Send GET_STATE or GET_CERT to inspect retained pre-crash state."
        elif self.reported_health in ("DOWN", "RECOVERING") and monitor_active:
            text = "Primary failure detected. Watch the monitor promote the standby."
        elif self.service_stopped_at is not None and monitor_active:
            text = f"Primary stopped. The monitor waits {self.timeout_seconds:g}s after the last heartbeat before failover."
        elif self.service_ready:
            text = "Primary is serving. Store a value with ENCRYPT_KEY, then compare GET_STATE before and after recovery."
        else:
            text = "Start Demo opens the monitor, standby, primary, then client. Stop terminal instances before starting."
        self.observation.set(text)

    def advance_startup(self):
        if not self.starting_demo:
            return
        for name in ("Monitor", "Backup", "Service"):
            child = self.children[name]
            if child.process is not None and not child.running:
                self.starting_demo = False
                return
        if self.monitor_ready and self.children["Backup"].process is None:
            self.starting_demo = self.start("Backup")
        if self.backup_ready and self.children["Service"].process is None:
            self.starting_demo = self.start("Service")
        if self.service_ready and self.children["Client"].process is None:
            self.start("Client")
            self.starting_demo = False

    def tick(self):
        for _ in range(500):
            try:
                name, process, line = self.events.get_nowait()
            except queue.Empty:
                break
            if self.children[name].process is not process:
                continue
            self.append_log(name, line)
            if name == "Monitor" and self.children[name].stop_deadline is None:
                self.read_monitor_event(line)
            elif name == "Backup" and "WARM STANDBY" in line:
                self.backup_ready = True
            elif name == "Service" and "Primary listening on " in line:
                self.service_ready = True
            elif name == "Client" and "Enter action: " in line:
                self.client_ready = True
            elif name == "Attack" and line.startswith(("Attack active;", "Attack reconnected.")):
                self.attack_active = True
        for name, child in self.children.items():
            if child.process is None:
                continue
            if child.running and child.stop_deadline is not None and time.monotonic() >= child.stop_deadline:
                self.signal_child(child, signal.SIGKILL)
                child.stop_deadline = time.monotonic() + 2
            elif not child.running and not child.exit_reported:
                child.exit_reported = True
                self.note(name, f"Exited with code {child.process.returncode}.")
                # Also clean up monitor-created cold backups and audit children.
                if name in ("Monitor", "Audit"):
                    self.signal_child(child, signal.SIGTERM)
                if name == "Monitor":
                    self.monitor_ready = self.monitor_connected = False
                elif name == "Service":
                    self.service_ready = False
                    self.service_stopped_at = time.monotonic()
                elif name == "Client":
                    self.client_ready = False
        self.advance_startup()
        self.update_display()
        if self.closing and not any(child.running for child in self.children.values()):
            self.root.destroy()
        else:
            self.root.after(POLL_MS, self.tick)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    project_root = args.project_root.expanduser().resolve()
    missing = [str(project_root / "code" / filename) for filename in SCRIPTS.values()
               if not (project_root / "code" / filename).is_file()]
    if missing:
        parser.error("Missing project scripts:\n" + "\n".join(missing))
    root = tk.Tk()
    DemoApp(root, project_root)
    root.mainloop()


if __name__ == "__main__":
    main()
