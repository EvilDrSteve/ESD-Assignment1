"""Unit tests for the HTTP caller's retry and circuit breaker, with a fake transport."""

import pytest
import requests

from http_caller import CallFailed, CircuitBreaker, CircuitOpenError, StoreClient


class FakeResponse:
    def __init__(self, status, headers=None, body=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FakeSession:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def request(self, *args, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make_client(outcomes, now):
    breaker = CircuitBreaker(failure_threshold=3, recovery_s=5, clock=lambda: now[0], on_change=lambda *a: None)
    client = StoreClient("http://x", breaker=breaker, sleep=lambda s: None)
    client.session = FakeSession(outcomes)
    return client


def test_retry_then_success():
    now = [0.0]
    client = make_client([requests.Timeout(), FakeResponse(201)], now)
    assert client.place_order("c", "key").status_code == 201
    assert client.session.calls == 2
    assert client.breaker.state == CircuitBreaker.CLOSED


def test_no_retry_without_idempotency_key():
    now = [0.0]
    client = make_client([FakeResponse(503)], now)
    with pytest.raises(CallFailed):
        client.place_order("c", None)
    assert client.session.calls == 1


def test_4xx_is_not_retried():
    now = [0.0]
    client = make_client([FakeResponse(409)], now)
    assert client.place_order("c", "key").status_code == 409
    assert client.session.calls == 1


def test_breaker_opens_blocks_and_recovers():
    now = [0.0]
    client = make_client([FakeResponse(503)] * 3 + [FakeResponse(503), FakeResponse(201)], now)
    with pytest.raises(CallFailed):
        client.place_order("c", "key")  # 3 failed attempts -> open
    assert client.breaker.state == CircuitBreaker.OPEN

    with pytest.raises(CircuitOpenError):
        client.place_order("c", "key")
    assert client.session.calls == 3  # blocked call never reached the server

    now[0] = 5.0  # recovery period over: one trial, it fails -> open again
    with pytest.raises(CircuitOpenError):
        client.place_order("c", "key")
    assert client.breaker.state == CircuitBreaker.OPEN

    now[0] = 10.0  # next trial succeeds -> closed
    assert client.place_order("c", "key").status_code == 201
    assert client.breaker.state == CircuitBreaker.CLOSED
