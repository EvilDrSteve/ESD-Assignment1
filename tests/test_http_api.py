"""HTTP API tests: valid request, invalid input, repeated request, rate limit."""

import uuid


def fill_cart(client, cart_id="cart-1", product_id="p1", quantity=2):
    r = client.put(f"/carts/{cart_id}/items/{product_id}", json={"quantity": quantity})
    assert r.status_code == 200, r.text
    return r.json()


# ── Valid requests ────────────────────────────────────────────────────────


def test_place_order_valid(client):
    fill_cart(client)
    r = client.post("/orders", json={"cart_id": "cart-1"}, headers={"Idempotency-Key": "k-1"})
    assert r.status_code == 201
    order = r.json()
    assert order["status"] == "pending"
    assert order["total"] == 59.98
    assert r.headers["Location"] == f"/orders/{order['id']}"
    assert client.get(r.headers["Location"]).json()["id"] == order["id"]
    # cart is emptied by checkout
    assert client.get("/carts/cart-1").json()["items"] == []


def test_set_cart_item_is_idempotent(client):
    fill_cart(client, quantity=3)
    cart = fill_cart(client, quantity=3)
    assert cart["items"][0]["quantity"] == 3


def test_get_unknown_cart_does_not_create_it(client):
    assert client.get("/carts/ghost").json() == {"id": "ghost", "items": [], "total": 0.0}
    assert client.get("/stats").json()["active_carts"] == 0


def test_status_transitions_and_repeat(client):
    fill_cart(client)
    order_id = client.post("/orders", json={"cart_id": "cart-1"}).json()["id"]
    assert client.patch(f"/orders/{order_id}", json={"status": "confirmed"}).json()["status"] == "confirmed"
    # repeating the same change is a safe no-op
    r = client.patch(f"/orders/{order_id}", json={"status": "confirmed"})
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    # skipping a step is rejected
    r = client.patch(f"/orders/{order_id}", json={"status": "delivered"})
    assert r.status_code == 409
    assert r.json()["type"] == "/problems/invalid-status-transition"


def test_cancel_restores_stock(client):
    fill_cart(client, product_id="p9", quantity=3)
    order_id = client.post("/orders", json={"cart_id": "cart-1"}).json()["id"]
    assert client.get("/products/p9").json()["stock"] == 0
    client.patch(f"/orders/{order_id}", json={"status": "cancelled"})
    assert client.get("/products/p9").json()["stock"] == 3


# ── Invalid input ─────────────────────────────────────────────────────────


def test_place_order_missing_cart_id(client):
    r = client.post("/orders", json={})
    assert r.status_code == 422
    assert r.headers["content-type"] == "application/problem+json"
    body = r.json()
    assert body["type"] == "/problems/validation-error"
    assert body["errors"][0]["field"] == "body.cart_id"
    assert body["request_id"] == r.headers["X-Request-ID"]


def test_place_order_bad_cart_id_format(client):
    r = client.post("/orders", json={"cart_id": "has spaces!"})
    assert r.status_code == 422


def test_place_order_empty_cart(client):
    r = client.post("/orders", json={"cart_id": "empty"})
    assert r.status_code == 409
    assert r.json()["type"] == "/problems/cart-empty"


def test_quantity_out_of_range(client):
    r = client.put("/carts/c/items/p1", json={"quantity": 0})
    assert r.status_code == 422


def test_unknown_product(client):
    r = client.put("/carts/c/items/nope", json={"quantity": 1})
    assert r.status_code == 404
    assert r.json()["type"] == "/problems/not-found"


def test_insufficient_stock(client):
    r = client.put("/carts/c/items/p9", json={"quantity": 4})
    assert r.status_code == 409
    assert r.json()["type"] == "/problems/insufficient-stock"


def test_unknown_order_status_value(client):
    r = client.patch("/orders/x", json={"status": "teleported"})
    assert r.status_code == 422


# ── Repeated requests ─────────────────────────────────────────────────────


def test_repeated_place_order_with_same_key_returns_same_order(client):
    fill_cart(client)
    key = str(uuid.uuid4())
    first = client.post("/orders", json={"cart_id": "cart-1"}, headers={"Idempotency-Key": key})
    second = client.post("/orders", json={"cart_id": "cart-1"}, headers={"Idempotency-Key": key})
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert second.headers["Idempotent-Replayed"] == "true"
    assert "Idempotent-Replayed" not in first.headers
    assert len(client.get("/orders").json()) == 1


def test_repeated_place_order_without_key_is_rejected_not_duplicated(client):
    fill_cart(client)
    assert client.post("/orders", json={"cart_id": "cart-1"}).status_code == 201
    # cart was emptied, so a blind repeat cannot create a duplicate either
    assert client.post("/orders", json={"cart_id": "cart-1"}).status_code == 409


def test_same_key_different_cart_is_rejected(client):
    fill_cart(client, cart_id="a")
    fill_cart(client, cart_id="b")
    client.post("/orders", json={"cart_id": "a"}, headers={"Idempotency-Key": "k"})
    r = client.post("/orders", json={"cart_id": "b"}, headers={"Idempotency-Key": "k"})
    assert r.status_code == 422
    assert r.json()["type"] == "/problems/idempotency-key-reused"


def test_failed_attempt_frees_the_key(client):
    r = client.post("/orders", json={"cart_id": "later"}, headers={"Idempotency-Key": "k2"})
    assert r.status_code == 409
    fill_cart(client, cart_id="later")
    r = client.post("/orders", json={"cart_id": "later"}, headers={"Idempotency-Key": "k2"})
    assert r.status_code == 201


def test_delete_cart_item_twice(client):
    fill_cart(client)
    assert client.delete("/carts/cart-1/items/p1").status_code == 204
    assert client.delete("/carts/cart-1/items/p1").status_code == 204


# ── Rate limit ────────────────────────────────────────────────────────────


def test_rate_limit_returns_429_with_retry_after(client):
    import main
    statuses = []
    for i in range(main.ORDERS_RATE_LIMIT + 2):
        fill_cart(client, cart_id=f"rl-{i}")
        r = client.post("/orders", json={"cart_id": f"rl-{i}"})
        statuses.append(r.status_code)
    assert statuses == [201] * main.ORDERS_RATE_LIMIT + [429, 429]
    assert 1 <= int(r.headers["Retry-After"]) <= main.ORDERS_RATE_WINDOW_S
    assert r.json()["type"] == "/problems/rate-limited"


def test_rate_limiter_window_resets():
    from ratelimit import FixedWindowLimiter, RateLimited
    now = [0.0]
    limiter = FixedWindowLimiter(2, 10, clock=lambda: now[0])
    limiter.hit("ip")
    limiter.hit("ip")
    try:
        limiter.hit("ip")
        assert False, "expected RateLimited"
    except RateLimited as e:
        assert e.retry_after == 10
    now[0] = 7.5
    try:
        limiter.hit("ip")
    except RateLimited as e:
        assert e.retry_after == 3
    limiter.hit("other-ip")  # separate clients have separate budgets
    now[0] = 10.0
    assert limiter.hit("ip") == 1


# ── Faults, request ids, metrics ──────────────────────────────────────────


def test_error_fault_returns_503(client):
    client.post("/admin/faults", json={"mode": "error", "duration_s": 5})
    r = client.get("/products")
    assert r.status_code == 503
    client.post("/admin/faults", json={"mode": "off"})
    assert client.get("/products").status_code == 200


def test_incoming_request_id_is_kept(client):
    r = client.get("/products/p1", headers={"X-Request-ID": "trace-abc-123"})
    assert r.headers["X-Request-ID"] == "trace-abc-123"


def test_metrics_exposed(client):
    fill_cart(client)
    client.post("/orders", json={"cart_id": "cart-1"})
    text = client.get("/metrics").text
    assert 'orders_placed_total{transport="http"}' in text
    assert "http_requests_in_flight" in text
    assert "orders_pending 1.0" in text
