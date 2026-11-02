# Side-by-Side GUI Demo

Run from the repository root using your activated environment:

```bash
python code/demo_gui.py
```

Stop project processes running in other terminals before starting. The GUI reads
the service and standby-control ports from `code/heartbeat_monitor_process.py`,
so it uses your current service port 5050 without changing other scripts' defaults.

## Failover

1. Click **Start Demo**. The GUI starts the monitor, waits for its listener,
   starts the standby, waits for readiness, then starts the primary and client.
   You can also start each process individually.
2. The primary, monitor, and standby logs stay visible next to each other.
   Crash probability defaults to **0.3** and the check interval to **3 seconds**.
   Set probability to **0** for a manual test; settings apply on the next start.
3. In the client panel, select **ENCRYPT_KEY**, enter a plaintext hash, and click
   **Send**. Copy the returned session key ID. Query **GET_STATE** before the crash.
4. Watch the primary crash, the monitor detect the missed heartbeats, and the
   standby take over. The status banner changes to **Standby serving**.
5. Query **GET_STATE** again, or paste the saved key ID and select **GET_CERT**,
   to compare the recovered state. Send is enabled when the client is ready.

## Audits

Click **Stop All** and wait for the demo processes to exit. In **Part 2 audits**,
choose **checkpoint** or **split-brain**, then click **Run**. The audit launches its
own isolated test processes; its output appears alongside the client panel.
The split-brain audit takes roughly 20–30 seconds. The Part 1 heartbeat attack
remains available in the neighboring tab.

The GUI only manages processes it launches. Stop All and closing the window also
signal their process groups on macOS/Linux, including monitor-created backups
and audit children. Existing terminal processes are left alone.

## Checks

```bash
python -m unittest discover -s tests -p test_demo_gui.py -v
```

This includes GUI command/status checks and a live recovery check using temporary
ports and checkpoints. The live check requires permission to open local sockets.
