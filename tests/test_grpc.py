"""gRPC service tests against a real in-process server and the shared (fake) Redis."""

import grpc
import pytest

import orders_pb2
import orders_pb2_grpc


@pytest.fixture
def channel():
    from grpc_server import build_server
    server = build_server(port=0)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    with grpc.insecure_channel(f"127.0.0.1:{port}") as ch:
        yield ch
    server.stop(None)


@pytest.fixture
def stub(channel):
    return orders_pb2_grpc.OrderServiceStub(channel)


def place(stub, cart_id, key="", request_id="test-rid"):
    return stub.PlaceOrder.with_call(
        orders_pb2.PlaceOrderRequest(cart_id=cart_id, idempotency_key=key),
        timeout=2, metadata=(("x-request-id", request_id),),
    )


def test_place_order_success_shares_data_with_http(stub, client):
    client.put("/carts/g1/items/p2", json={"quantity": 1})
    response, call = place(stub, "g1")
    assert response.order.status == orders_pb2.PENDING
    assert response.order.total == 89.99
    assert dict(call.initial_metadata())["x-request-id"] == "test-rid"
    # the order created over gRPC is visible over HTTP: one source of truth
    assert client.get(f"/orders/{response.order.id}").json()["total"] == 89.99


def test_empty_cart_is_failed_precondition(stub):
    with pytest.raises(grpc.RpcError) as e:
        place(stub, "nothing-here")
    assert e.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    assert dict(e.value.trailing_metadata())["error-type"] == "cart-empty"


def test_bad_cart_id_is_invalid_argument(stub):
    with pytest.raises(grpc.RpcError) as e:
        place(stub, "bad id!")
    assert e.value.code() == grpc.StatusCode.INVALID_ARGUMENT


def test_idempotency_shared_across_transports(stub, client):
    client.put("/carts/g2/items/p1", json={"quantity": 1})
    first, _ = place(stub, "g2", key="shared-key")
    # the same key over HTTP replays the order made over gRPC
    r = client.post("/orders", json={"cart_id": "g2"}, headers={"Idempotency-Key": "shared-key"})
    assert r.status_code == 201 and r.json()["id"] == first.order.id
    again, _ = place(stub, "g2", key="shared-key")
    assert again.replayed and again.order.id == first.order.id


def test_grpc_metrics_recorded(stub):
    from prometheus_client import REGISTRY
    with pytest.raises(grpc.RpcError):
        place(stub, "empty-cart")
    value = REGISTRY.get_sample_value("grpc_server_handled_total", {
        "grpc_service": "store.v1.OrderService", "grpc_method": "PlaceOrder",
        "grpc_code": "FAILED_PRECONDITION",
    })
    assert value and value >= 1


# ── A3: read-only GetProduct and the health service ──────────────────────


def test_get_product_matches_http(stub, client):
    product = stub.GetProduct(orders_pb2.GetProductRequest(product_id="p2"), timeout=2)
    assert product.name == "Mechanical Keyboard" and product.price == 89.99
    http = client.get("/products/p2").json()
    assert (product.id, product.category, product.stock) == (http["id"], http["category"], http["stock"])


def test_get_unknown_product_is_not_found(stub):
    with pytest.raises(grpc.RpcError) as e:
        stub.GetProduct(orders_pb2.GetProductRequest(product_id="nope"), timeout=2)
    assert e.value.code() == grpc.StatusCode.NOT_FOUND
    assert dict(e.value.trailing_metadata())["error-type"] == "not-found"


@pytest.mark.parametrize("service", ["", "store.v1.OrderService"])
def test_health_check_serving(channel, service):
    from grpc_health.v1 import health_pb2, health_pb2_grpc
    reply = health_pb2_grpc.HealthStub(channel).Check(health_pb2.HealthCheckRequest(service=service), timeout=2)
    assert reply.status == health_pb2.HealthCheckResponse.SERVING
