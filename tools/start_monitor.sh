#!/bin/bash
pkill -f night_monitor.py
nohup python3 /tmp/night_monitor.py > /tmp/monitor_out.log 2>&1 &
echo "Monitor PID: $!"
