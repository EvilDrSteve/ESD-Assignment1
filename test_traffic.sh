#!/bin/bash
# Background traffic for the dashboards (A2 routes). Ctrl+C to stop.
# Note: POST /orders is rate limited (5 per 10 s), so some orders get 429.
BASE="http://localhost:8050"
i=0

while true; do
  i=$((i + 1))
  CART="cart-$i"

  # Browse products
  curl -s "$BASE/products" > /dev/null
  curl -s "$BASE/products/p1" > /dev/null

  # Put items in the cart (leaves active_carts > 0 between rounds)
  curl -s -X PUT "$BASE/carts/$CART/items/p1" -H "Content-Type: application/json" -d '{"quantity":1}' > /dev/null
  curl -s -X PUT "$BASE/carts/$CART/items/p2" -H "Content-Type: application/json" -d '{"quantity":1}' > /dev/null

  # Place order every other round (keeps some carts active)
  if [ $((i % 2)) -eq 0 ]; then
    RESP=$(curl -s -X POST "$BASE/orders" -H "Content-Type: application/json" \
           -H "Idempotency-Key: traffic-$i-$RANDOM" -d "{\"cart_id\":\"$CART\"}")
    ORDER=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)

    if [ -n "$ORDER" ]; then
      # Leave some pending, confirm some, cancel some
      case $((i % 5)) in
        0) curl -s -X PATCH "$BASE/orders/$ORDER" -H "Content-Type: application/json" -d '{"status":"cancelled"}' > /dev/null ;;
        2) curl -s -X PATCH "$BASE/orders/$ORDER" -H "Content-Type: application/json" -d '{"status":"confirmed"}' > /dev/null ;;
        *) ;; # leave pending
      esac
    fi
  fi

  # Stats and health
  curl -s "$BASE/stats" > /dev/null
  curl -s "$BASE/health" > /dev/null

  echo "Round $i"
  sleep 0.5
done
