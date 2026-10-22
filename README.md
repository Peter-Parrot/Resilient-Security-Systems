# Resilient-Security-Systems

## Objective Part One:  
Within this part one of our mini group project, we will create a lightweight security service protocol and simulate a realistic,
non-deterministic system failure/crash.  
**Part 1**  
We will design and implement a monitoring agent that reliably distinguishes between healthy operations and process crashes (fault detection).

### MEMBER LIST:  
- Peter-Parrot 
- BusterShrugz
- esheffield48 (Ethan Sheffield)
---

# Project Instructions

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
