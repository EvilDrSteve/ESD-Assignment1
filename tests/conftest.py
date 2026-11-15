import os
import sys

import fakeredis
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, os.path.join(ROOT, "app", "gen"))
sys.path.insert(0, os.path.join(ROOT, "clients"))

os.environ.setdefault("ENABLE_FAULT_ADMIN", "true")
os.environ.setdefault("REPLICA", "T")  # skip the reverse-DNS lookup outside Docker

import store as store_module  # noqa: E402

# One in-memory Redis shared by the HTTP app and the gRPC servicer, like the real deployment
FAKE_REDIS = fakeredis.FakeRedis(decode_responses=True)
store_module.store.r = FAKE_REDIS


@pytest.fixture(autouse=True)
def clean_store():
    FAKE_REDIS.flushall()
    store_module.store.seed()  # flushall also cleared the rate-limit counters
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    import main
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def channel():
    """A real gRPC server on a free port, sharing the fake Redis with the HTTP app."""
    import grpc
    from grpc_server import build_server
    server = build_server(port=0)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    with grpc.insecure_channel(f"127.0.0.1:{port}") as ch:
        yield ch
    server.stop(None)


@pytest.fixture
def stub(channel):
    import orders_pb2_grpc
    return orders_pb2_grpc.OrderServiceStub(channel)
