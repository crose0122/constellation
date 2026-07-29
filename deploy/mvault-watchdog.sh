#!/usr/bin/env bash
# Kill pipeline stages that have stopped making progress.
#
# vision_http.py makes a hung *vision call* impossible. This catches everything
# else — a DB lock that never resolves, ffmpeg wedged on a malformed video, a
# PIL decode that never returns — because the failure that cost 22 hours was
# not special: any stage that neither finishes nor fails holds up every job
# queued behind it, and the queue is how work gets done unattended.
#
# Detection is CPU time, not log output. A wedged process burns no CPU, and
# that is true whatever wedged it — the incident showed 1d02h elapsed against
# 19 minutes of CPU. Log-based detection would need to know each stage's log
# format and would miss a stage that hangs while holding a buffered line.
#
# The threshold must exceed the longest LEGITIMATE stall. A stage blocked on
# GPU inference also burns no local CPU, so the floor is the vision ceiling
# (900s in vision_http) plus margin. 25 minutes is comfortably above it and
# far below the hours a real hang costs.
#
#   nohup ~/mvault-watchdog.sh >> ~/watchdog.log 2>&1 &
#   STALL_MINUTES=40 ~/mvault-watchdog.sh        # more patient
set -uo pipefail

STALL_MINUTES="${STALL_MINUTES:-25}"
POLL_SECONDS="${POLL_SECONDS:-60}"
# Only stages that call out to the vision server or chew media. `brain` is the
# web server — it is idle by design and must never be killed.
WATCH_RE="mvault (tag|screen|describe|faces|ingest|curate|dedup|discover)"

declare -A last_cpu last_change

cpu_seconds() {                     # total CPU (utime+stime) in seconds
  local pid=$1 stat utime stime
  stat=$(cat "/proc/$pid/stat" 2>/dev/null) || return 1
  # fields 14,15 after the (comm) field, which may itself contain spaces
  stat=${stat#*") "}
  utime=$(echo "$stat" | awk '{print $12}')
  stime=$(echo "$stat" | awk '{print $13}')
  [ -z "$utime" ] && return 1
  echo $(( (utime + stime) / $(getconf CLK_TCK) ))
}

echo "[$(date -Is)] watchdog up — stall threshold ${STALL_MINUTES}m, poll ${POLL_SECONDS}s"

while true; do
  now=$(date +%s)
  seen=""
  while read -r pid cmd; do
    [ -z "$pid" ] && continue
    seen="$seen $pid"
    cpu=$(cpu_seconds "$pid") || continue
    if [ "${last_cpu[$pid]:-unset}" = "unset" ]; then
      last_cpu[$pid]=$cpu; last_change[$pid]=$now; continue
    fi
    if [ "$cpu" -ne "${last_cpu[$pid]}" ]; then
      last_cpu[$pid]=$cpu; last_change[$pid]=$now; continue
    fi
    stalled=$(( (now - last_change[$pid]) / 60 ))
    if [ "$stalled" -ge "$STALL_MINUTES" ]; then
      echo "[$(date -Is)] STALLED ${stalled}m with no CPU: pid=$pid $cmd"
      echo "[$(date -Is)]   sockets: $(ss -tnp 2>/dev/null | grep -c "pid=$pid") open"
      kill -TERM "$pid" 2>/dev/null
      sleep 10
      if kill -0 "$pid" 2>/dev/null; then
        echo "[$(date -Is)]   SIGTERM ignored, sending SIGKILL"
        kill -KILL "$pid" 2>/dev/null
      fi
      echo "[$(date -Is)]   killed pid=$pid — the job's own error handling takes over"
      unset "last_cpu[$pid]" "last_change[$pid]"
    fi
  done < <(pgrep -af "$WATCH_RE" | grep -v "watchdog" | awk '{pid=$1; $1=""; print pid, $0}')

  # forget pids that have exited, so the table cannot grow without bound
  for pid in "${!last_cpu[@]}"; do
    case " $seen " in *" $pid "*) ;; *) unset "last_cpu[$pid]" "last_change[$pid]";; esac
  done
  sleep "$POLL_SECONDS"
done
