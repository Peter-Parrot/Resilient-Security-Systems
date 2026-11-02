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
