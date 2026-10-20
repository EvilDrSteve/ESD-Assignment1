import os
import sys

import fakeredis
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, os.path.join(ROOT, "app", "gen"))
sys.path.insert(0, os.path.join(ROOT, "clients"))

os.environ.setdefault("ENABLE_FAULT_ADMIN", "true")

import store as store_module  # noqa: E402

# One in-memory Redis shared by the HTTP app and the gRPC servicer, like the real deployment
FAKE_REDIS = fakeredis.FakeRedis(decode_responses=True)
store_module.store.r = FAKE_REDIS


@pytest.fixture(autouse=True)
def clean_store():
    FAKE_REDIS.flushall()
    store_module.store.seed()
    import main
    main.orders_limiter.reset()
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    import main
    with TestClient(main.app) as c:
        yield c
