#!/bin/sh
# Runs INSIDE the restricted container profile. Exits nonzero on any boundary
# violation. CI runs this before every untrusted stage; a PASS is required.
set -u
fail() { echo "CANARY FAIL: $1"; exit 1; }

[ "$(id -u)" = "0" ] && fail "running as root"
[ -e /var/run/docker.sock ] && fail "docker socket visible"
nets=$(ls /sys/class/net 2>/dev/null | grep -v '^lo$' | wc -l)
[ "$nets" -ne 0 ] && fail "non-loopback network interfaces present"
env | grep -Eq 'GITHUB_|ACTIONS_|RUNNER_|TOKEN|SECRET|PASSWORD' \
  && fail "CI or credential environment variables visible"
# Check the mount flag itself: a DAC permission error would mask a writable root.
awk '$2 == "/" {print $4}' /proc/mounts | grep -q '^ro,\|,ro,\|,ro$\|^ro$' \
  || fail "root filesystem not mounted read-only"
caps=$(grep CapEff /proc/self/status | awk '{print $2}')
[ "$caps" != "0000000000000000" ] && fail "capabilities not dropped (CapEff=$caps)"
[ -d /submissions ] && fail "full submissions tree visible"
echo "CANARY PASS: uid=$(id -u) no-net no-caps no-socket no-creds ro-root"
