#!/usr/bin/env bash
# Sample memory, swap and disk on the runner and report the peaks.
#
#   resource-monitor.sh start    begin sampling every 5s in the background
#   resource-monitor.sh report   stop sampling and write the peaks, df and
#                                docker's own usage to the step summary
#
# A hosted runner has 16 GB of RAM and one root filesystem. A build that
# gets close to either limit fails in ways that look like compiler or
# BuildKit errors, so every heavy job records how close it came. The
# sampler outlives the step that starts it; the runner kills it at job end
# if report never runs.
set -euo pipefail

log=${RUNNER_TEMP:-/tmp}/resource-monitor.log
pidfile=${log}.pid

sample() {
  # MiB: memory in use (total - available), swap in use, disk used on /.
  awk '/^MemTotal:/ {mt=$2} /^MemAvailable:/ {ma=$2} /^SwapTotal:/ {st=$2} /^SwapFree:/ {sf=$2}
       END {printf "%d %d ", (mt-ma)/1024, (st-sf)/1024}' /proc/meminfo
  df -m --output=used / | tail -1 | tr -d ' '
}

case "${1:-}" in
  start)
    : > "${log}"
    (
      while :; do
        echo "$(date +%s) $(sample)" >> "${log}"
        sleep 5
      done
    ) > /dev/null 2>&1 &
    echo $! > "${pidfile}"
    echo "sampling to ${log} (pid $!)"
    ;;

  report)
    if [[ -f "${pidfile}" ]]; then
      kill "$(cat "${pidfile}")" 2> /dev/null || true
      rm -f "${pidfile}"
    fi
    echo "$(date +%s) $(sample)" >> "${log}"

    mem_total=$(awk '/^MemTotal:/ {printf "%d", $2/1024}' /proc/meminfo)
    swap_total=$(awk '/^SwapTotal:/ {printf "%d", $2/1024}' /proc/meminfo)
    read -r disk_total disk_avail < <(df -m --output=size,avail / | tail -1)
    read -r samples minutes mem_peak swap_peak disk_peak disk_end < <(awk '
      NR == 1 {t0 = $1}
      {n++; t1 = $1; if ($2 > m) m = $2; if ($3 > s) s = $3; if ($4 > d) d = $4; e = $4}
      END {printf "%d %d %d %d %d %d\n", n, (t1 - t0) / 60, m, s, d, e}' "${log}")

    gib() { awk -v m="$1" 'BEGIN {printf "%.1f GiB", m / 1024}'; }
    pct() { awk -v a="$1" -v b="$2" 'BEGIN {printf "%d%%", b ? 100 * a / b : 0}'; }

    out=${GITHUB_STEP_SUMMARY:-/dev/stdout}
    {
      echo "## Runner resources"
      echo
      echo "${samples} samples over ${minutes} min."
      echo
      echo "| | peak | of | |"
      echo "|---|---|---|---|"
      echo "| memory | $(gib "${mem_peak}") | $(gib "${mem_total}") | $(pct "${mem_peak}" "${mem_total}") |"
      echo "| swap | $(gib "${swap_peak}") | $(gib "${swap_total}") | $(pct "${swap_peak}" "${swap_total}") |"
      echo "| disk used on / | $(gib "${disk_peak}") | $(gib "${disk_total}") | $(pct "${disk_peak}" "${disk_total}") |"
      echo "| disk used at end | $(gib "${disk_end}") | | $(gib "${disk_avail}") free |"
      echo
      echo "<details><summary>df, docker system df</summary>"
      echo
      echo '```'
      df -h / /tmp "${RUNNER_TEMP:-/tmp}" 2> /dev/null | awk '!seen[$0]++'
      echo
      docker system df 2> /dev/null || true
      echo '```'
      echo
      echo "</details>"
    } >> "${out}"

    # Not failures: the job's own steps decide that. A warning on the run
    # page is enough to notice a build creeping towards a limit.
    if (( mem_peak * 100 > mem_total * 90 )); then
      echo "::warning::peak memory $(gib "${mem_peak}") is over 90% of $(gib "${mem_total}")"
    fi
    if (( swap_peak > 512 )); then
      echo "::warning::the runner swapped $(gib "${swap_peak}") at peak"
    fi
    if (( disk_total - disk_peak < 10240 )); then
      echo "::warning::less than 10 GiB of disk was left free at peak"
    fi
    ;;

  *)
    echo "usage: $0 start|report" >&2
    exit 2
    ;;
esac
