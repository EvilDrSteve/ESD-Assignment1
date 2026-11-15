"""gRPC front end for the order service.

In the container it runs inside the same process as the HTTP app (serve.py
calls build_server()). `python grpc_server.py` still runs it on its own, with
metrics on METRICS_PORT.

PlaceOrder calls the same orders_service.place_order() as HTTP POST /orders,
against the same Redis, so both transports share rules and data.
"""

import os
import signal
import sys
from concurrent import futures

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "gen"))

import grpc  # noqa: E402
import redis  # noqa: E402
from grpc_health.v1 import health, health_pb2, health_pb2_grpc  # noqa: E402
from grpc_reflection.v1alpha import reflection  # noqa: E402
from prometheus_client import start_http_server  # noqa: E402

import orders_pb2  # noqa: E402
import orders_pb2_grpc  # noqa: E402
import orders_service as svc  # noqa: E402
from errors import DomainError  # noqa: E402
from grpc_observability import ObservabilityInterceptor  # noqa: E402
from logger import logger  # noqa: E402
from models import Order  # noqa: E402
from store import store  # noqa: E402

GRPC_PORT = int(os.environ.get("GRPC_PORT", "50051"))
METRICS_PORT = int(os.environ.get("METRICS_PORT", "9101"))
# Calls handled at the same time; a slowed-down replica (A3 Part D) holds a
# thread for the whole delay, so leave headroom.
GRPC_WORKERS = int(os.environ.get("GRPC_WORKERS", "32"))
ORDER_SERVICE = orders_pb2.DESCRIPTOR.services_by_name["OrderService"].full_name


def to_proto(order: Order) -> orders_pb2.Order:
    msg = orders_pb2.Order(
        id=order.id,
        cart_id=order.cart_id,
        total=order.total,
        status=orders_pb2.OrderStatus.Value(order.status.upper()),
        items=[orders_pb2.OrderItem(**item.model_dump()) for item in order.items],
    )
    msg.created_at.FromDatetime(order.created_at)
    msg.updated_at.FromDatetime(order.updated_at)
    return msg


def call_service(context: grpc.ServicerContext, fn, *args, **kwargs):
    """Run shared business code; map its errors to gRPC status codes."""
    try:
        return fn(*args, **kwargs)
    except DomainError as e:
        context.set_trailing_metadata((("error-type", e.type),))
        context.abort(getattr(grpc.StatusCode, e.grpc_code), e.detail)
    except redis.RedisError:
        logger.exception("store_unavailable")
        context.set_trailing_metadata((("error-type", "service-unavailable"),))
        context.abort(grpc.StatusCode.UNAVAILABLE, "Order store is unavailable; retry later")


class OrderServicer(orders_pb2_grpc.OrderServiceServicer):
    def PlaceOrder(self, request: orders_pb2.PlaceOrderRequest, context: grpc.ServicerContext):
        order, replayed = call_service(
            context, svc.place_order, request.cart_id, request.idempotency_key or None, transport="grpc"
        )
        return orders_pb2.PlaceOrderResponse(order=to_proto(order), replayed=replayed)

    def GetProduct(self, request: orders_pb2.GetProductRequest, context: grpc.ServicerContext):
        product = call_service(context, svc.get_product, request.product_id)
        return orders_pb2.Product(**product.model_dump())


def add_health_service(server: grpc.Server) -> health.HealthServicer:
    """Standard grpc.health.v1.Health: Check returns SERVING while the replica runs.

    "" is the overall server status; Envoy's gRPC health check asks for it.
    """
    servicer = health.HealthServicer()
    for service in ("", ORDER_SERVICE):
        servicer.set(service, health_pb2.HealthCheckResponse.SERVING)
    health_pb2_grpc.add_HealthServicer_to_server(servicer, server)
    return servicer


def build_server(port: int = GRPC_PORT) -> grpc.Server:
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=GRPC_WORKERS),
        interceptors=[ObservabilityInterceptor()],
    )
    orders_pb2_grpc.add_OrderServiceServicer_to_server(OrderServicer(), server)
    add_health_service(server)
    # Reflection lets tools like grpcurl list and call the services without the .proto
    reflection.enable_server_reflection(
        (ORDER_SERVICE, health.SERVICE_NAME, reflection.SERVICE_NAME),
        server,
    )
    server.add_insecure_port(f"[::]:{port}")
    return server


def serve():
    store.seed()
    start_http_server(METRICS_PORT)
    server = build_server()
    server.start()
    logger.info("startup", extra={"grpc_port": GRPC_PORT, "metrics_port": METRICS_PORT})

    def stop(*_):
        logger.info("shutdown")
        server.stop(grace=5)

    signal.signal(signal.SIGTERM, stop)
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
