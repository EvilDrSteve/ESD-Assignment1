"""Call OrderService.PlaceOrder directly over gRPC.

Examples
  python clients/grpc_client.py --cart-id cart-1
  python clients/grpc_client.py --cart-id cart-1 --key my-key-1 --deadline 0.5
  python clients/grpc_client.py --cart-id "bad id!"          # INVALID_ARGUMENT

The request id is sent as x-request-id metadata, so the server's log lines for
this call can be found in Kibana with app.request_id.
"""

import argparse
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "gen"))

import grpc  # noqa: E402
from google.protobuf.json_format import MessageToDict  # noqa: E402

import orders_pb2  # noqa: E402
import orders_pb2_grpc  # noqa: E402


def place_order(target: str, cart_id: str, key: str | None, deadline_s: float, request_id: str) -> int:
    with grpc.insecure_channel(target) as channel:
        stub = orders_pb2_grpc.OrderServiceStub(channel)  # generated client stub
        request = orders_pb2.PlaceOrderRequest(cart_id=cart_id, idempotency_key=key or "")
        start = time.perf_counter()
        try:
            response, call = stub.PlaceOrder.with_call(
                request,
                timeout=deadline_s,  # becomes the gRPC deadline, sent to the server
                metadata=(("x-request-id", request_id),),
            )
        except grpc.RpcError as e:
            elapsed = (time.perf_counter() - start) * 1000
            trailing = dict(e.trailing_metadata() or ())
            print(json.dumps({
                "ok": False,
                "grpc_code": e.code().name,
                "details": e.details(),
                "error_type": trailing.get("error-type"),
                "request_id": request_id,
                "elapsed_ms": round(elapsed, 1),
            }, indent=2))
            return 1

        elapsed = (time.perf_counter() - start) * 1000
        print(json.dumps({
            "ok": True,
            "grpc_code": "OK",
            "replayed": response.replayed,
            "order": MessageToDict(response.order, preserving_proto_field_name=True),
            "request_id": dict(call.initial_metadata()).get("x-request-id"),
            "elapsed_ms": round(elapsed, 1),
        }, indent=2))
        return 0


def main():
    parser = argparse.ArgumentParser(description="gRPC client for store.v1.OrderService/PlaceOrder")
    parser.add_argument("--target", default=os.environ.get("GRPC_TARGET", "localhost:50051"))
    parser.add_argument("--cart-id", required=True)
    parser.add_argument("--key", help="idempotency key (optional)")
    parser.add_argument("--deadline", type=float, default=2.0, help="deadline in seconds (default 2.0)")
    parser.add_argument("--request-id", default=None, help="x-request-id to propagate (default: random)")
    args = parser.parse_args()

    request_id = args.request_id or f"grpc-cli-{uuid.uuid4()}"
    sys.exit(place_order(args.target, args.cart_id, args.key, args.deadline, request_id))


if __name__ == "__main__":
    main()
