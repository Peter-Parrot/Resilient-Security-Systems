# Resilient-Security-Systems

## Objective Part One:  
Within this part one of our mini group project, we will create a lightweight security service protocol and simulate a realistic,
non-deterministic system failure/crash.  
**Part 1**  
We will design and implement a monitoring agent that reliably distinguishes between healthy operations and process crashes (fault detection).

### MEMBER LIST:  
- Peter-Parrot 
- BusterShrugz (Reese Edens)
- esheffield48 (Ethan Sheffield)
- aidan-palmer
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
3. Once the service is connected and `ONLINE`, choose `spoof`, `replay`, or `false-liveness` and click **Attack → Start**. Keep the attack interval at 2 seconds and duration at 30 seconds. The attack shows `Armed; waiting to take over` while the real service owns the monitor connection. Start the service before the attack so the attacker doesn't claim the empty slot first.
4. Every 5 seconds, the service has a 10% chance of terminating its entire process with exit code 1. Look for `Injected stochastic crash` in the Service log. The expected wait is 50 seconds, but the actual wait is random and has no fixed upper bound.
5. Keep the monitor and attack running. The attacker retries every half second and automatically takes over after the service disconnects. It shows `Active; sending fake heartbeats`. Its 30-second duration starts at takeover, excluding the wait. The monitor normally stays `ONLINE` through the crash because fake heartbeats arrive before its 15-second timeout.
6. After the attack ends, the monitor reports `DOWN` about 15 seconds after the last fake heartbeat. Stop the attack before restarting the service. To demonstrate ordinary crash detection without spoofing, repeat with the attack stopped.

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

---

For this mini group project, you will create a lightweight security service protocol and simulate a realistic, non-deterministic system failure/crash. In Part 1, you will design and implement a monitoring agent that reliably distinguishes between healthy operations and process crashes (fault detection).

You may use any languages or libraries.

## Task 1: Team Selection

You will work in teams of 3-4. Form a team with your classmates. You may use the Canvas discussion board to find teammates.

## Task 2: Select a Security Domain

Choose a lightweight security service for your prototype. Keep the functional logic minimal (a basic prototype is expected); the focus is on the monitoring mechanism. Examples include:

*   **Authentication/Ticket Service:** Validates dummy credentials or issues session tokens.
*   **Network Sentinel/Firewall Agent:** Inspects dummy packet payloads and flags blacklisted IPs.
*   **Cryptographic Key-Storage Daemon:** Responds to requests for public certificates or encrypted session wrappers.
*   **Integrity Monitor/Log Aggregator:** Collects and hashes simulated event logs.

## Task 3: Implement the Critical Process with Simulated Failure

Implement the core service as an independent running process. The process must experience unpredictable crashes during normal execution.

**Rule:** Do not hard-code an arbitrary static conditional (e.g., `if (request_count == 5) exit()`). Implement stochastic or environmental crash triggers, such as:

*   A random probability trigger on incoming requests (e.g., simulating a memory leak or segfault on malformed input).
*   A randomized background fault-injection thread.

The failure must terminate/exit the process completely (do not simply put the process to sleep).

## Task 4: Implement the Heartbeat Monitor

Implement an independent Monitor Process that runs separately from the critical service (separate processes/sockets). The monitored service and the monitor must communicate periodic liveness signals:

*   **Option A (Push/Periodic Heartbeat):** The critical service sends a "HEARTBEAT" datagram to the monitor every $T$ milliseconds.
*   **Option B (Pull/Ping-Ack):** The monitor pings the service every $T$ milliseconds, requiring a timely response.

**Fault Detection Logic:**

*   Define an explicit timeout threshold (e.g., missing $N$ consecutive beats or exceeding a sliding window timeout).
*   When the critical service crashes, the monitor must detect the failure, print an alert to standard output with timestamps, and log the incident.
*   The monitor must not crash when the monitored service dies.

## Task 5: Adversarial Assessment

At least one team member should take on the role of an adversarial auditor to analyze and test vulnerabilities in the heartbeat channel:

*   **Attack 1 – Heartbeat Spoofing:** Can an external script transmit fake heartbeat messages to the monitor so that the monitor reports the service is healthy even after the critical process has crashed?
*   **Attack 2 – Replay Attack/Tampering:** If the heartbeat payload includes status data or a timestamp, can an attacker capture a valid packet and replay it indefinitely to mask downtime?

## Reflection (individual)

Take a few minutes to reflect on your personal workflow, the concepts you encountered, and the tools you used to complete this assignment.

1.  How difficult was this assignment on a scale of 1 (very easy) – 5 (very challenging)?
2.  What was easy about this assignment and why?
3.  What was challenging about this assignment and why?
4.  What external tools or resources did you use to complete this assignment and where were they helpful?

## Deliverables

Please submit the following via Canvas:

1.  **Each team must submit a single repository link** (e.g., GitHub/GitLab) containing the following:
    *   **a. Critical Service Process:** Clean, documented source code for the lightweight security service featuring the non-deterministic crash injection mechanism.
    *   **b. Heartbeat Monitor Process:** Source code for the independent watchdog agent that manages timers/windows, processes incoming heartbeats, and triggers explicit failure alerts upon a crash.
    *   **c. Adversarial Audit Script:** A standalone script or utility designed to attempt heartbeat spoofing, message replay, or false liveness injection against the monitor.
    * **d. Role Distribution:** A separate `docs/CONTRIBUTIONS.md` listing each team member's specific contributions to the service, monitor, security audit, and video demo.
2.  **Each team must submit a single PDF report** containing:
    *   **a. Names of all teammates:** First and last.
    *   **b. Service Overview:** A concise description of your team's chosen security domain and the simulated failure scenario.
    *   **c. Execution & Setup Instructions:** Unambiguous, step-by-step terminal commands to compile, configure, and execute the service, monitor, and attack script.
    *   **d. Architectural Parameter Justification:**
        *   **i.** Heartbeat pattern (Push vs. Pull/Ping-Echo).
        *   **ii.** Timing calibration: Values chosen for heartbeat interval ($T$), timeout window ($W$), and missed-beat threshold ($N$), justifying how your team balanced detection speed (MTTD) against false positives.
    *   **e. Adversarial Audit Summary:** 1-2 paragraphs detailing the vulnerability of the baseline heartbeat, and whether or not the attack script exploited it successfully and why.
3.  **Each team must submit a single video demonstration** that meets the following criteria:
    *   **a. Time Limit:** Strictly under 3 minutes (anything over will not be reviewed). If Canvas doesn’t allow the video upload, please provide a Google Drive or unlisted YouTube link.
    *   **b.** Show both processes starting up and the monitor acknowledging valid heartbeats in real time.
    *   **c.** Show the critical service triggering its non-deterministic crash (you may need to edit the video to show it, since there’s a time limit on the video but the crash is non-deterministic).
    *   **d.** Show the attacker script executing against the monitor (demonstrating either the exploit succeeding or failing on the build).
    *   **e. Voiceover:** Explain what is going on.
4.  **Each individual (separately in Canvas) must submit their reflection** with the four reflection questions.

*Note: For team deliverables, one person may submit. For individual reflections, each individual must submit.*

## Grading

Here is the grading scheme for the project report:

*   Service script (15)
*   Heartbeat script (15)
*   Adversarial script (15)
*   PDF report (20)
*   Video demo (20)
*   Individual reflection (15)

**TOTAL: 100 points**
