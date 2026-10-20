#!/bin/bash
# Repeatable mix of successful and failing Place-order calls over HTTP and gRPC,
# to populate the Grafana A2 dashboard. Run from the repo root with the dev
# venv active:  bash scripts/gen_calls.sh [rounds]
#
# Each round takes 11 s, longer than the 10 s rate-limit window, so the
# 3-4 HTTP POSTs of one round never share a window with the next round and
# stay under 5 per window (run scripts/rate_limit_demo.sh to see 429s):
#   HTTP : 1 success (201), 1 empty cart (409), 1 bad body (422)
#   gRPC : 1 success (OK), 1 empty cart (FAILED_PRECONDITION), 1 bad id (INVALID_ARGUMENT)
# Every 5th round adds one idempotent replay on each transport.
BASE="${STORE_URL:-http://localhost:8050}"
ROUNDS="${1:-20}"
ROUND_S=11
PY="${PYTHON:-python}"
JSON="Content-Type: application/json"

grpc_code() { grep -o '"grpc_code": "[A-Z_]*"' | cut -d'"' -f4; }

for i in $(seq 1 "$ROUNDS"); do
  START=$SECONDS
  # HTTP: success (with idempotency key) and failures
  CART="gen-h-$i-$RANDOM"
  KEY="gen-key-$i-$RANDOM"
  curl -s -o /dev/null -X PUT "$BASE/carts/$CART/items/p$(( (i % 8) + 1 ))" -H "$JSON" -d '{"quantity":2}'
  H_OK=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/orders" -H "$JSON" -H "Idempotency-Key: $KEY" -d "{\"cart_id\":\"$CART\"}")
  H_REP="-"
  if (( i % 5 == 0 )); then
    H_REP=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/orders" -H "$JSON" -H "Idempotency-Key: $KEY" -d "{\"cart_id\":\"$CART\"}")
  fi
  H_EMPTY=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/orders" -H "$JSON" -d "{\"cart_id\":\"empty-$i\"}")
  H_BAD=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/orders" -H "$JSON" -d '{"cart":"oops"}')

  # gRPC: success and failures (cart filled over HTTP: same Redis)
  GCART="gen-g-$i-$RANDOM"
  curl -s -o /dev/null -X PUT "$BASE/carts/$GCART/items/p$(( ((i + 3) % 8) + 1 ))" -H "$JSON" -d '{"quantity":1}'
  G_OK=$("$PY" clients/grpc_client.py --cart-id "$GCART" --key "g-$KEY" | grpc_code)
  G_REP="-"
  if (( i % 5 == 0 )); then
    G_REP=$("$PY" clients/grpc_client.py --cart-id "$GCART" --key "g-$KEY" | grep -o '"replayed": [a-z]*' | cut -d' ' -f2)
  fi
  G_EMPTY=$("$PY" clients/grpc_client.py --cart-id "empty-g-$i" | grpc_code)
  G_BAD=$("$PY" clients/grpc_client.py --cart-id "bad id!" | grpc_code)

  echo "round $i/$ROUNDS  http: ok=$H_OK replay=$H_REP empty=$H_EMPTY bad=$H_BAD" \
       " grpc: ok=$G_OK replay=$G_REP empty=$G_EMPTY bad=$G_BAD"
  sleep $(( ROUND_S - (SECONDS - START) > 0 ? ROUND_S - (SECONDS - START) : 0 ))
done
