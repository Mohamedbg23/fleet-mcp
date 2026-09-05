#!/usr/bin/env bash
# Run with sudo on any VM whose port 22 faces the internet.
#
#   sudo ./harden-ssh.sh [your.home.ip.address]
#
# Why this exists: a public SSH port absorbs a constant brute-force flood.
# OpenSSH's default MaxStartups (10:30:100) lets as few as 10 concurrent
# pre-auth connections starve the listener -- real logins then get
# "Exceeded MaxStartups", or simply hang after the TCP handshake with no
# banner at all. That looks exactly like a dead machine. It isn't: sshd
# just has no free slots. Raise the ceiling and ban repeat offenders.
set -euo pipefail
ADMIN_IP="${1:-}"

echo "== sshd limits"
tee /etc/ssh/sshd_config.d/90-hardening.conf >/dev/null <<'CONF'
MaxStartups 100:30:200
MaxAuthTries 3
LoginGraceTime 20
PermitRootLogin no
PasswordAuthentication no
CONF
sshd -t
systemctl reload ssh 2>/dev/null || systemctl reload sshd
pgrep -a sshd | grep -o 'of [0-9-]* startups' | head -1

echo "== fail2ban"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq fail2ban

tee /etc/fail2ban/jail.local >/dev/null <<CONF
[DEFAULT]
# Never ban your own network or your admin IP.
ignoreip = 127.0.0.1/8 ::1 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 ${ADMIN_IP}
backend  = systemd
bantime  = 1h
findtime = 10m
maxretry = 4

[sshd]
# 'aggressive' also catches "Connection closed by authenticating user",
# which is the pattern the pre-auth flood bots actually produce. The
# default filter misses it entirely.
mode = aggressive
enabled = true
# Ubuntu 24.04 runs sshd under ssh.service, not sshd.service.
journalmatch = _SYSTEMD_UNIT=ssh.service + _COMM=sshd
CONF

systemctl enable --now fail2ban
systemctl restart fail2ban
sleep 5
fail2ban-client status sshd | grep -E 'Total failed|Currently banned' || true
echo "done on $(hostname)"