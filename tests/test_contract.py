"""Use the committed openapi.yaml to test POST /orders: every response must match
the schema the spec declares for its status code and content type."""

import os

import jsonschema
import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

with open(os.path.join(ROOT, "openapi.yaml"), encoding="utf-8") as f:
    SPEC = yaml.safe_load(f)


def check_against_spec(path: str, method: str, response):
    op = SPEC["paths"][path][method]
    status = str(response.status_code)
    assert status in op["responses"], f"{status} is not documented for {method.upper()} {path}"
    media_type = response.headers["content-type"].split(";")[0]
    content = op["responses"][status]["content"]
    assert media_type in content, f"{media_type} not documented for {status}"
    schema = {**content[media_type]["schema"], "components": SPEC["components"]}
    jsonschema.validate(response.json(), schema)
    for header in op["responses"][status].get("headers", {}):
        if header in ("Idempotent-Replayed",):  # only sent on replays
            continue
        assert header in response.headers, f"documented header {header} missing"


@pytest.mark.parametrize("scenario", ["created", "empty-cart", "invalid", "rate-limited"])
def test_place_order_matches_spec(client, scenario):
    import main
    if scenario == "created":
        client.put("/carts/c1/items/p1", json={"quantity": 1})
        r = client.post("/orders", json={"cart_id": "c1"}, headers={"Idempotency-Key": "spec-1"})
    elif scenario == "empty-cart":
        r = client.post("/orders", json={"cart_id": "nothing"})
    elif scenario == "invalid":
        r = client.post("/orders", json={"cart_id": 42, "extra": True})
    else:
        for _ in range(main.ORDERS_RATE_LIMIT):
            client.post("/orders", json={"cart_id": "nothing"})
        r = client.post("/orders", json={"cart_id": "nothing"})
    check_against_spec("/orders", "post", r)
