# Implement an independent Monitor Process that runs separately from the critical
# service (separate processes/sockets). The monitored service and the monitor must
# communicate periodic liveness signals:
#  Option A (Push/Periodic Heartbeat): The critical service sends a "HEARTBEAT"
# datagram to the monitor every T milliseconds.
#  Option B (Pull/Ping-Ack): The monitor pings the service every T milliseconds,
# requiring a timely response.
#  Define an explicit timeout threshold (e.g., missing N consecutive beats or
# exceeding a sliding window timeout).
#  When the critical service crashes, the monitor must detect the failure, print an
# alert to standard output with timestamps, and log the incident.
#  The monitor must not crash when the monitored service dies.