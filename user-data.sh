#!/bin/bash
# Set MTU 9000 for Jumbo Frames
ip link set dev eth0 mtu 9000
# Kernel tuning for low latency
sysctl -w net.core.busy_poll=50
sysctl -w net.core.busy_read=50
sysctl -w net.ipv4.tcp_fastopen=3
sysctl -w net.core.rmem_max=134217728
sysctl -w net.core.wmem_max=134217728
# Install Docker
apt-get update
apt-get install -y docker.io docker-compose-v2
systemctl enable --now docker
