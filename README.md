# Resilient-Security-Systems

## Objective Part One:  
Within this part one of our mini group project, we will create a lightweight security service protocol and simulate a realistic,
non-deterministic system failure/crash.  
**Part 1**  
We will design and implement a monitoring agent that reliably distinguishes between healthy operations and process crashes (fault detection).

### MEMBER LIST:  
- Peter-Parrot (Evan Zamore)
- BusterShrugz (Reese Edens)
- esheffield48 (Ethan Sheffield)
- aidan-palmer (Aidan Palmer)
---

# Project Testing Instructions

## Running the demo GUI

Run commands from the repository root using the project's virtual environment.
Install dependencies with `.venv/bin/python -m pip install -r requirements.txt` if needed.

### GUI

```bash
.venv/bin/python code/demo_gui.py
```

1. Stop any monitor, service, or attack instances already running in your IDE or terminals. The GUI tracks only processes it launches, and duplicate listeners cannot share the same ports.
2. Click **Monitor → Start**, then **Service → Start**. The service listens for key requests on TCP `127.0.0.1:5000` and sends WebSocket heartbeats to the monitor on `127.0.0.1:8000` every 5 seconds.
3. Once the service is connected and `ONLINE`, click **Client → Start** to start the client process. Choose `LIST_KEYS`, `GET_CERT`, or `ENCRYPT_KEY` to determine the message type. The `Key ID` dropdown allows you to select which key/certificate to retrieve in `GET_CERT` messages. For `ENCRYPT_KEY` messages, you can either put in the plaintext to encrypt in the daemon, or an empty line with generate a random plaintext sequence. 
4. Once the service is connected and `ONLINE`, choose `spoof`, `replay`, or `false-liveness` and click **Attack → Start**. Keep the attack interval at 2 seconds and duration at 30 seconds. The attack shows `Armed; waiting to take over` while the real service owns the monitor connection. Start the service before the attack so the attacker doesn't claim the empty slot first.
5. Every 5 seconds, the service has a 10% chance of terminating its entire process with exit code 1. Look for `Injected stochastic crash` in the Service log. The expected wait is 50 seconds, but the actual wait is random and has no fixed upper bound.
6. Keep the monitor and attack running. The attacker retries every half second and automatically takes over after the service disconnects. It shows `Active; sending fake heartbeats`. Its 30-second duration starts at takeover, excluding the wait. The monitor normally stays `ONLINE` through the crash because fake heartbeats arrive before its 15-second timeout.
7. After the attack ends, the monitor reports `DOWN` about 15 seconds after the last fake heartbeat. Stop the attack before restarting the service. To demonstrate ordinary crash detection without spoofing, repeat with the attack stopped.

The service's **Stop** button is a manual termination; use the random crash for the assignment's stochastic-failure demonstration. Replay uses a real heartbeat previously recorded in `heartbeat.log`. The baseline heartbeat contains only `{"type": "heartbeat"}`, so its replay is identical to a minimal spoof: the monitor authenticates neither the sender nor message freshness. The single-connection restriction blocks a second client while the real service is connected, but does not authenticate a replacement after it disconnects.

### Separate terminals / PyCharm

Start the monitor in one terminal:

```bash
.venv/bin/python code/heartbeat_monitor_process.py
```

Start the service in a second terminal:

```bash
.venv/bin/python code/critical_service_process.py 127.0.0.1 5000
```

Start the client in a third terminal:

```bash
.venv/bin/python code/client.py 127.0.0.1 5000
```

While the service is connected, arm one attack in a third terminal. It will wait until the service disconnects, then take over automatically:

```bash
.venv/bin/python code/adversarial_audit_script.py spoof --interval 2 --duration 30
# Or replay a previously logged genuine heartbeat:
.venv/bin/python code/adversarial_audit_script.py replay --interval 2 --duration 30 --log heartbeat.log
# Or send only the minimum accepted heartbeat:
.venv/bin/python code/adversarial_audit_script.py false-liveness --interval 2 --duration 30
```

In PyCharm, use the repository root as the working directory for all three configurations and `.venv/bin/python` as the interpreter. Service parameters are `127.0.0.1 5000`; audit parameters can be `spoof --interval 2 --duration 30`; the monitor needs no parameters. The service also defaults to `127.0.0.1:5000` when run without parameters.

For key-service debugging without injected faults, append `--crash-probability 0` to the service command. For a shorter but still random demo, use `--crash-probability 0.25 --crash-interval 5` (20-second expected wait, not a guaranteed deadline).
