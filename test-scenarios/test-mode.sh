#!/usr/bin/env bash
# Start or stop the dedicated test gateways.
#
# Usage:
#   ./test-mode.sh start          — start 3-agent profiles (hermes9/10/11)
#   ./test-mode.sh start 8        — start 8-agent profiles (hermes4-11)
#   ./test-mode.sh stop           — stop all test gateways
#   ./test-mode.sh restart        — stop then start (same agent count)
#   ./test-mode.sh restart 8      — stop then start 8-agent set

set -euo pipefail

CMD="${1:-start}"
AGENT_COUNT="${2:-3}"

if [[ "$AGENT_COUNT" == "8" ]]; then
  TEST_PROFILES=(hermes4 hermes5 hermes6 hermes7 hermes8 hermes9 hermes10 hermes11)
else
  TEST_PROFILES=(hermes9 hermes10 hermes11)
fi

stop_gateways() {
  echo "Stopping test gateways..."
  for prof in "${TEST_PROFILES[@]}"; do
    # Kill by profile name pattern
    pgrep -f "hermes.*--profile $prof" | xargs kill -9 2>/dev/null || true
  done
  sleep 1
}

setup_scenario_log_env() {
  # Mint a per-run directory and export the env vars that the
  # scenario_log module checks. Each gateway started below inherits
  # them. Production runs leave HERMES_SCENARIO_LOG_DIR unset and
  # scenario_log.log() is a no-op.
  RUN_ID="scn-$(date +%Y%m%d-%H%M%S)"
  LOG_DIR="/tmp/hermes-scenario-runs/$RUN_ID"
  mkdir -p "$LOG_DIR"
  export HERMES_SCENARIO_LOG_DIR="$LOG_DIR"
  export HERMES_SCENARIO_RUN_ID="$RUN_ID"
  ln -sfn "$LOG_DIR" /tmp/hermes-scenario-runs/latest
  echo "Scenario log dir: $LOG_DIR"
  echo "  (symlinked at /tmp/hermes-scenario-runs/latest)"
}

start_gateways() {
  echo "Starting test gateways..."
  for prof in "${TEST_PROFILES[@]}"; do
    nohup env HERMES_PROFILE="$prof" arch -arm64 hermes --profile "$prof" gateway run --replace \
      > "/tmp/${prof}-gw.log" 2>&1 &
    echo "$!" > "/tmp/${prof}-gw.pid"
    echo "  $prof started"
  done
  sleep 5
  echo "Gateway status:"
  for prof in "${TEST_PROFILES[@]}"; do
    log="$HOME/.hermes/profiles/$prof/logs/gateway.log"
    if [[ -f "$log" ]]; then
      aap_ok=$(grep -c "✓ aap connected" "$log" 2>/dev/null || echo 0)
      echo "  $prof: aap=$aap_ok"
    fi
  done
}

verify_scenario_log_env() {
  echo "Verifying scenario log env reached each gateway..."
  local missing=0
  for prof in "${TEST_PROFILES[@]}"; do
    pid="$(cat "/tmp/${prof}-gw.pid" 2>/dev/null || true)"
    if [[ -z "$pid" ]]; then
      echo "  $prof: pid file not found"
      missing=1
      continue
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "  $prof: gateway process not running ($pid)"
      missing=1
      continue
    fi
    if ps eww "$pid" | tr ' ' '\n' | grep -q "^HERMES_SCENARIO_LOG_DIR=$HERMES_SCENARIO_LOG_DIR$"; then
      echo "  $prof: ok ($pid)"
    else
      echo "  $prof: missing HERMES_SCENARIO_LOG_DIR ($pid)"
      missing=1
    fi
  done
  if [[ "$missing" -ne 0 ]]; then
    echo ""
    echo "Scenario log env verification failed. Stop any launchd-managed gateways and retry."
    exit 1
  fi
}

case "$CMD" in
  start)
    setup_scenario_log_env
    start_gateways
    verify_scenario_log_env
    echo ""
    echo "Test environment ready. Drive the scenario manually through /tmp/hermes-loopback/hN-in.txt and hN-out.txt."
    ;;
  stop)
    stop_gateways
    echo "Test gateways stopped."
    ;;
  restart)
    stop_gateways
    setup_scenario_log_env
    start_gateways
    verify_scenario_log_env
    ;;
  *)
    echo "Usage: $0 start|stop|restart"
    exit 1
    ;;
esac
