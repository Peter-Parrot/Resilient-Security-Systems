# At least one team member should take on the role of an adversarial auditor to analyze
# and test vulnerabilities in the heartbeat channel:
#  Attack 1 – Heartbeat Spoofing: Can an external script transmit fake heartbeat
# messages to the monitor so that the monitor reports the service is healthy even
# after the critical process has crashed?
#  Attack 2 – Replay Attack/Tampering: If the heartbeat payload includes status data
# or a timestamp, can an attacker capture a valid packet and replay it indefinitely
# to mask downtime?