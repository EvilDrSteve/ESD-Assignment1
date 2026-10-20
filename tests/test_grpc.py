"""gRPC service tests against a real in-process server and the shared (fake) Redis."""

import grpc
import pytest

import orders_pb2
import orders_pb2_grpc


@pytest.fixture
def stub():
    from grpc_server import build_server
    server = build_server(port=0)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
        yield orders_pb2_grpc.OrderServiceStub(channel)
    server.stop(None)


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
