#!/bin/bash
# Ctrl+C to stop
BASE="http://localhost:8050"
i=0

while true; do
  i=$((i + 1))
  CART="cart-$i"

  # Browse products
  curl -s "$BASE/products" > /dev/null
  curl -s "$BASE/products/p1" > /dev/null

  # Add items to cart (leaves active_carts > 0 between rounds)
  curl -s -X POST "$BASE/cart/$CART/items" -H "Content-Type: application/json" -d '{"product_id":"p1","quantity":1}' > /dev/null
  curl -s -X POST "$BASE/cart/$CART/items" -H "Content-Type: application/json" -d '{"product_id":"p2","quantity":1}' > /dev/null

  # Place order every other round (keeps some carts active)
  if [ $((i % 2)) -eq 0 ]; then
    RESP=$(curl -s -X POST "$BASE/orders?cart_id=$CART")
    ORDER=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)

    if [ -n "$ORDER" ]; then
      # Leave some pending, confirm some, cancel some
      case $((i % 5)) in
        0) curl -s -X PUT "$BASE/orders/$ORDER/cancel" > /dev/null ;;
        2) curl -s -X PUT "$BASE/orders/$ORDER/confirm" > /dev/null ;;
        *) ;; # leave pending
      esac
    fi
  fi

  # Stats and health
  curl -s "$BASE/stats" > /dev/null
  curl -s "$BASE/health" > /dev/null

  echo "Round $i"
  sleep 0.1
done