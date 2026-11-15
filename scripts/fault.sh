#!/bin/bash
# Turn a fault on or off for one replica (A3) or all of them.
# Goes through Envoy; whichever replica answers stores it in the shared Redis,
# and the target replica picks it up on its next request.
#
#   bash scripts/fault.sh B delay            # B waits 200 ms before every request (10 min)
#   bash scripts/fault.sh B delay 300 120    # 300 ms for 120 s
#   bash scripts/fault.sh B off
#   bash scripts/fault.sh all error 10       # every replica returns 503 / UNAVAILABLE for 10 s
#   bash scripts/fault.sh all status         # injected delay on every replica now (from Prometheus)
set -e
BASE="${STORE_URL:-http://localhost:8050}"
REPLICA="${1:?usage: fault.sh <A|B|C|all> <delay|error|slow|off|status> [delay_ms] [duration_s]}"
MODE="${2:?mode: delay, error, slow, off or status}"
DELAY_MS="${3:-200}"
DURATION_S="${4:-600}"

if [ "$MODE" = "status" ]; then
  # Read the gauge straight from Prometheus, which scrapes every replica
  curl -s "${PROM_URL:-http://localhost:9090}/api/v1/query" \
       --data-urlencode "query=injected_delay_seconds" | python3 -m json.tool
  exit 0
fi

case "$MODE" in
  off)   BODY="{\"mode\":\"off\",\"replica\":\"$REPLICA\"}" ;;
  error) BODY="{\"mode\":\"error\",\"replica\":\"$REPLICA\",\"duration_s\":${3:-10}}" ;;
  *)     BODY="{\"mode\":\"$MODE\",\"replica\":\"$REPLICA\",\"delay_ms\":$DELAY_MS,\"duration_s\":$DURATION_S}" ;;
esac

# Retries cover Envoy being recreated a moment ago (connection refused/reset)
curl -s --retry 5 --retry-all-errors --retry-delay 1 -X POST "$BASE/admin/faults" -H "Content-Type: application/json" -d "$BODY"
echo
