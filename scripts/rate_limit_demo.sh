#!/bin/bash
# Send 7 checkouts in a row to POST /orders (limit: 5 per 10 s per client IP).
# Expect 5 x 201, then 429 with Retry-After; after waiting, calls work again.
BASE="${STORE_URL:-http://localhost:8050}"

checkout() {
  local cart="rl-$RANDOM$RANDOM"
  curl -s -o /dev/null -X PUT "$BASE/carts/$cart/items/p4" -H "Content-Type: application/json" -d '{"quantity":1}'
  curl -s -o /dev/null -D - -X POST "$BASE/orders" -H "Content-Type: application/json" \
       -d "{\"cart_id\":\"$cart\"}" \
    | grep -i -E "^HTTP/|^retry-after|^x-ratelimit-remaining" | tr -d '\r' | paste -sd ' '
}

for i in $(seq 1 7); do
  printf "%s call %d: " "$(date +%T)" "$i"; checkout
done

# Show one full 429 response body (problem details)
echo "--- 429 body:"
RESP_HEADERS=$(mktemp)
curl -s -D "$RESP_HEADERS" -X POST "$BASE/orders" -H "Content-Type: application/json" -d '{"cart_id":"x"}'
echo
RETRY=$(grep -i '^retry-after' "$RESP_HEADERS" | tr -d '\r' | awk '{print $2}')
rm -f "$RESP_HEADERS"

echo "$(date +%T) waiting ${RETRY:-10}s (Retry-After) ..."
sleep "${RETRY:-10}"
printf "%s call after wait: " "$(date +%T)"; checkout
