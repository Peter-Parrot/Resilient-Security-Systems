## Steps to Demonstrate Failover and Recovery

```bash
python code/heartbeat_monitor_process.py
python code/backup_service_process.py --standby
python code/critical_service_process.py --crash-probability 0.3 --crash-interval 3
```

## Run the Adversarial Audit Script

```bash
python code/adversarial_audit_part2.py checkpoint
python code/adversarial_audit_part2.py split-brain
```

## Part Two — Task 2: Set up
# Passive Redundancy Demo

This demo runs a primary service, a warm standby, a heartbeat monitor, and a client. When the primary crashes randomly, the monitor promotes the standby automatically.

## Setup

Run all commands from the project root. Install dependencies once:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

In `code/heartbeat_monitor_process.py`, set:

```python
SERVICE_PORT = 5050
```

Port `5050` avoids the macOS Control Center conflict on port `5000`. The standby uses port `5001` for promotion signals.

## Start the Processes

Stop previous project instances first. Open four terminals and start these **in order**.

**Terminal 1 — Heartbeat Monitor**

```bash
.venv/bin/python code/heartbeat_monitor_process.py
```

Expected: the monitor listens on port `8000`.

**Terminal 2 — Primary Service**

```bash
.venv/bin/python code/critical_service_process.py 127.0.0.1 5050 --crash-probability 0.1 --crash-interval 5
```

Wait until it reports listening on port `5050`. The monitor should report `ONLINE`.

The primary has a 10% crash probability per heartbeat check, approximately every five seconds. The crash time is random.

**Terminal 3 — Warm Standby**

```bash
.venv/bin/python code/backup_service_process.py 127.0.0.1 5050 --standby
```

Expected: the standby waits for a promotion signal on port `5001`.

**Terminal 4 — Client**

```bash
.venv/bin/python code/client.py 127.0.0.1 5050
```

Enter `LIST_KEYS`. Expected key IDs:

```text
["key1", "key2", "key3"]
```

## Observe Automatic Recovery

Leave all processes running until the primary crashes.

1. Heartbeats stop.
2. After approximately 15 seconds without a heartbeat, the monitor reports `DOWN` and `[Failover Triggered]`.
3. The standby receives the promotion signal and takes over port `5050`.
4. The monitor reports `[Service Restored]`.
5. The client reconnects. Enter `LIST_KEYS` again to verify service availability.

For a controlled test, start the primary with `--crash-probability 0`, then press Ctrl+C in Terminal 2.

## Troubleshooting

For `Address already in use`, identify listeners:

```bash
lsof -nP -iTCP:5050 -iTCP:5001 -sTCP:LISTEN
```

Stop leftover project processes before restarting. Ensure the monitor, primary, standby, and client all use service port `5050`.
