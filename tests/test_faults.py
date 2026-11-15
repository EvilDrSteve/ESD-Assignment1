"""Per-replica faults (A3): fault:<replica> slows or breaks one replica only.

Tests run as replica "T" (conftest), so a fault for "B" must not touch them.
"""

import time

import grpc
import pytest

import orders_pb2

DELAY_MS = 150


def timed(fn):
    start = time.perf_counter()
    result = fn()
    return result, (time.perf_counter() - start) * 1000


def set_fault(client, **body):
    r = client.post("/admin/faults", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_delay_on_this_replica_slows_http_but_not_health(client):
    reply = set_fault(client, mode="delay", replica="T", delay_ms=DELAY_MS, duration_s=30)
    assert reply["replica"] == "T" and reply["delay_ms"] == DELAY_MS and reply["expires_in_ms"] > 0
    r, ms = timed(lambda: client.get("/products/p1"))
    assert r.status_code == 200 and ms >= DELAY_MS
    _, ms = timed(lambda: client.get("/health"))
    assert ms < DELAY_MS  # a slow replica still passes health checks


def test_delay_on_another_replica_does_not_touch_this_one(client):
    set_fault(client, mode="delay", replica="B", delay_ms=1000, duration_s=30)
    _, ms = timed(lambda: client.get("/products/p1"))
    assert ms < 500


def test_replica_fault_wins_over_all(client):
    set_fault(client, mode="error", replica="all", duration_s=30)
    assert client.get("/products/p1").status_code == 503
    set_fault(client, mode="delay", replica="T", delay_ms=1, duration_s=30)
    assert client.get("/products/p1").status_code == 200
    set_fault(client, mode="off", replica="T")
    assert client.get("/products/p1").status_code == 503
    set_fault(client, mode="off")
    assert client.get("/products/p1").status_code == 200


def test_delay_applies_to_grpc_but_not_grpc_health(client, stub, channel):
    from grpc_health.v1 import health_pb2, health_pb2_grpc
    set_fault(client, mode="delay", replica="T", delay_ms=DELAY_MS, duration_s=30)
    _, ms = timed(lambda: stub.GetProduct(orders_pb2.GetProductRequest(product_id="p1"), timeout=2))
    assert ms >= DELAY_MS
    health = health_pb2_grpc.HealthStub(channel)
    _, ms = timed(lambda: health.Check(health_pb2.HealthCheckRequest(), timeout=2))
    assert ms < DELAY_MS


def test_error_fault_is_unavailable_over_grpc(client, stub):
    set_fault(client, mode="error", replica="T", duration_s=30)
    with pytest.raises(grpc.RpcError) as e:
        stub.GetProduct(orders_pb2.GetProductRequest(product_id="p1"), timeout=2)
    assert e.value.code() == grpc.StatusCode.UNAVAILABLE


def test_injected_delay_gauge(client):
    from prometheus_client import REGISTRY
    assert REGISTRY.get_sample_value("injected_delay_seconds") == 0
    set_fault(client, mode="delay", replica="T", delay_ms=250, duration_s=30)
    assert REGISTRY.get_sample_value("injected_delay_seconds") == 0.25


def test_bad_replica_name_rejected(client):
    assert client.post("/admin/faults", json={"mode": "delay", "replica": "b"}).status_code == 422
