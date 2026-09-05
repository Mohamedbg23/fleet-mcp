#!/usr/bin/env bash
# Adaptive Oracle "Always Free" idle-reclamation guard.
#
# Oracle reclaims an Always Free VM only if, over a 7-day window, its
# 95th-percentile CPU AND network AND memory are ALL under 20%. Because it is
# an AND, holding a single metric above the line is enough -- we hold CPU.
#
# CONDITIONAL BY DESIGN: every WINDOW seconds this measures what the box is
# actually doing and burns only the shortfall. If your own workload is already
# above TARGET it burns nothing at all. Together with Nice=19 / CPUWeight=1 the
# kernel also hands any cycle straight back to real work the moment it asks.
set -uo pipefail

TARGET=${TARGET:-35}          # % total CPU to hold (Oracle's threshold is 20)
MAXDUTY=${MAXDUTY:-70}        # hard cap on our share of any one window
WINDOW=${WINDOW:-30}          # control-loop period, seconds
MEM_TARGET=${MEM_TARGET:-30}  # % memory to hold on A1; set 0 to disable
MEM_FLOOR=${MEM_FLOOR:-25}    # dump ballast if available memory falls below this %

NCPU=$(nproc)
BAL_DIR=/run/keepalive
BAL=$BAL_DIR/ballast
[ "$(uname -m)" = aarch64 ] || MEM_TARGET=0   # Oracle's memory test is A1-only

prev_idle=0 prev_tot=0 busy=0
sample() {                     # sets $busy = % CPU busy since the last call
  local f i tot idle dt di
  read -ra f < /proc/stat
  idle=$(( f[4] + f[5] ))
  tot=0; for i in "${f[@]:1}"; do tot=$(( tot + i )); done
  dt=$(( tot - prev_tot )); di=$(( idle - prev_idle ))
  if (( prev_tot > 0 && dt > 0 )); then busy=$(( (100 * (dt - di)) / dt )); fi
  prev_tot=$tot prev_idle=$idle
}

burn() {                       # $1 = seconds of load, one worker per vCPU
  local s=$1 pids=() i
  (( s <= 0 )) && return 0
  for (( i = 0; i < NCPU; i++ )); do
    timeout "$s" bash -c 'while :; do :; done' & pids+=($!)
  done
  wait "${pids[@]}" 2>/dev/null
  return 0
}

ballast_mb() { echo $(( $(stat -c %s "$BAL" 2>/dev/null || echo 0) / 1048576 )); }

ballast_setup() {
  (( MEM_TARGET == 0 )) && return 0
  mkdir -p "$BAL_DIR"
  if mountpoint -q "$BAL_DIR"; then mount -o remount,size=50% "$BAL_DIR"
  else mount -t tmpfs -o size=50% tmpfs "$BAL_DIR"; fi
  : > "$BAL"
}

ballast_adjust() {             # grow/shrink so total memory lands near MEM_TARGET
  (( MEM_TARGET == 0 )) && return 0
  local total used cur real want
  read -r total used < <(free -m | awk '/^Mem:/{print $2, $3}')
  cur=$(ballast_mb); real=$(( used - cur ))
  want=$(( total * MEM_TARGET / 100 - real ))
  (( want < 0 )) && want=0
  (( want > total * 40 / 100 )) && want=$(( total * 40 / 100 ))
  (( want > cur && want - cur < 64 )) && return 0    # ignore small churn
  (( cur > want && cur - want < 64 )) && return 0
  if (( want > cur )); then
    dd if=/dev/zero of="$BAL" bs=1M seek="$cur" count=$(( want - cur )) \
       conv=notrunc status=none 2>/dev/null
  else
    truncate -s "${want}M" "$BAL" 2>/dev/null
  fi
  return 0
}

mem_guard() {                  # emergency valve, faster than the control loop
  (( MEM_TARGET == 0 )) && return 0
  local total avail
  while :; do
    read -r total avail < <(free -m | awk '/^Mem:/{print $2, $7}')
    if (( total > 0 && avail * 100 / total < MEM_FLOOR )); then
      truncate -s 0 "$BAL" 2>/dev/null
    fi
    sleep 5
  done
}

cleanup() { pkill -P $$ 2>/dev/null; truncate -s 0 "$BAL" 2>/dev/null; }
trap cleanup EXIT

ballast_setup
mem_guard &
duty=$TARGET
sample
while :; do
  b=$(( WINDOW * duty / 100 ))
  burn "$b"
  sleep $(( WINDOW - b ))
  sample
  duty=$(( duty + TARGET - busy ))     # integral control: converges in one step
  (( duty < 0 )) && duty=0
  (( duty > MAXDUTY )) && duty=$MAXDUTY
  ballast_adjust
  logger -t oci-keepalive "busy=${busy}% duty=${duty}% ballast=$(ballast_mb)MB"
done
