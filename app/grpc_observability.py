"""Server interceptor: request id propagation, Prometheus metrics and access logs for gRPC.

It wraps every unary handler, so the servicer stays free of observability code
(the gRPC counterpart of the HTTP middleware in main.py).
"""

import re
import time
import uuid

import grpc
from prometheus_client import Counter, Gauge, Histogram

from logger import logger, request_id_var, transport_var
from metrics import LATENCY_BUCKETS

REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

grpc_server_handled_total = Counter(
    "grpc_server_handled_total",
    "RPCs completed on the server, by method and gRPC status code",
    ["grpc_service", "grpc_method", "grpc_code"],
)

grpc_server_handling_seconds = Histogram(
    "grpc_server_handling_seconds",
    "Time from the handler receiving an RPC to it returning, measured server-side",
    ["grpc_service", "grpc_method"],
    buckets=LATENCY_BUCKETS,
)

grpc_server_in_flight = Gauge(
    "grpc_server_in_flight",
    "RPCs currently being handled",
    ["grpc_service", "grpc_method"],
)


def split_method(full_method: str) -> tuple[str, str]:
    # "/store.v1.OrderService/PlaceOrder" -> ("store.v1.OrderService", "PlaceOrder")
    _, service, method = full_method.split("/", 2)
    return service, method


class ObservabilityInterceptor(grpc.ServerInterceptor):
    def intercept_service(self, continuation, handler_call_details):
        handler = continuation(handler_call_details)
        if handler is None or handler.unary_unary is None:
            return handler

        service, method = split_method(handler_call_details.method)
        metadata = dict(handler_call_details.invocation_metadata or ())
        inner = handler.unary_unary

        def observed(request, context: grpc.ServicerContext):
            incoming = metadata.get("x-request-id", "")
            request_id = incoming if REQUEST_ID_RE.match(incoming) else str(uuid.uuid4())
            rid_token = request_id_var.set(request_id)
            transport_token = transport_var.set("grpc")
            context.send_initial_metadata((("x-request-id", request_id),))

            in_flight = grpc_server_in_flight.labels(grpc_service=service, grpc_method=method)
            in_flight.inc()
            start = time.perf_counter()
            code = grpc.StatusCode.OK
            try:
                response = inner(request, context)
                code = context.code() or grpc.StatusCode.OK
                return response
            except Exception:
                # context.abort() sets the code before raising; anything else is a bug -> UNKNOWN
                code = context.code() or grpc.StatusCode.UNKNOWN
                if code == grpc.StatusCode.UNKNOWN:
                    logger.exception("grpc_unhandled_error")
                raise
            finally:
                duration = time.perf_counter() - start
                in_flight.dec()
                grpc_server_handling_seconds.labels(grpc_service=service, grpc_method=method).observe(duration)
                grpc_server_handled_total.labels(
                    grpc_service=service, grpc_method=method, grpc_code=code.name
                ).inc()
                log = logger.info if code == grpc.StatusCode.OK else logger.warning
                log("rpc", extra={
                    "request_id": request_id,
                    "rpc_method": handler_call_details.method,
                    "grpc_code": code.name,
                    "duration_ms": round(duration * 1000, 2),
                    "deadline_left_ms": None if context.time_remaining() is None
                    else round(context.time_remaining() * 1000),
                })
                request_id_var.reset(rid_token)
                transport_var.reset(transport_token)

        return grpc.unary_unary_rpc_method_handler(
            observed,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
