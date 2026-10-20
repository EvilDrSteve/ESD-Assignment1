from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from contextlib import asynccontextmanager
from typing import Annotated
import asyncio
import os
import re
import time
import uuid

from pydantic import BaseModel, Field
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, Counter as PromCounter, Gauge

import faults
import orders_service as svc
from errors import DomainError
from logger import logger, request_id_var, transport_var
from metrics import (
    http_requests_total, http_request_duration_seconds, http_requests_in_flight,
    http_rate_limited_total,
)
from models import (
    Cart, Order, OrderStatus, PlaceOrderRequest, Problem, Product, SetCartItem,
    UpdateOrderStatus,
)
from ratelimit import FixedWindowLimiter, RateLimited
from store import store

demo_good = PromCounter("demo_requests_total_good", "Demo counter without high-cardinality label")
demo_bad = PromCounter("demo_requests_total_bad", "Demo counter with high-cardinality label", ["request_id"])

# Read from Redis when Prometheus scrapes, so the value is right whichever
# process (HTTP or gRPC) changed the data.
def _from_redis(read):
    def value():
        try:
            return read()
        except Exception:  # Redis down: report "unknown" instead of failing the scrape
            return float("nan")
    return value


orders_pending = Gauge("orders_pending", "Number of orders currently pending")
orders_pending.set_function(_from_redis(lambda: store.count_orders(OrderStatus.PENDING)))
active_carts = Gauge("active_carts", "Number of carts with items")
active_carts.set_function(_from_redis(store.count_active_carts))

# A1 experiment: every 5th request slow, every 7th fails
FAULT_INJECTION = os.environ.get("FAULT_INJECTION", "false").lower() == "true"
request_counter = 0

ORDERS_RATE_LIMIT = int(os.environ.get("ORDERS_RATE_LIMIT", "5"))
ORDERS_RATE_WINDOW_S = int(os.environ.get("ORDERS_RATE_WINDOW_S", "10"))
orders_limiter = FixedWindowLimiter(ORDERS_RATE_LIMIT, ORDERS_RATE_WINDOW_S)

REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
NO_FAULT_PATHS = ("/health", "/metrics", "/admin/faults", "/docs", "/openapi.json")


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.seed()
    logger.info("startup", extra={"service_port": 8050})
    yield
    logger.info("shutdown")


app = FastAPI(
    title="Online Store API",
    description=(
        "Online store HTTP API (ESD Assignment 2). Every error uses RFC 9457 problem details "
        "(`application/problem+json`). Each response carries `X-Request-ID`; send your own to "
        "correlate logs across services."
    ),
    version="2.0.0",
    lifespan=lifespan,
)


# ── Error format ──────────────────────────────────────────────────────────


def problem(status: int, type_: str, title: str, detail: str, request: Request,
            errors: list | None = None, headers: dict | None = None) -> JSONResponse:
    body = Problem(
        type=f"/problems/{type_}", title=title, status=status, detail=detail,
        instance=request.url.path, request_id=request_id_var.get(), errors=errors,
    )
    return JSONResponse(
        status_code=status,
        content=body.model_dump(exclude_none=True),
        media_type="application/problem+json",
        headers=headers,
    )


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError):
    return problem(exc.http_status, exc.type, exc.title, exc.detail, request)


@app.exception_handler(RateLimited)
async def rate_limited_handler(request: Request, exc: RateLimited):
    return problem(
        429, "rate-limited", "Too many requests", str(exc), request,
        headers={
            "Retry-After": str(exc.retry_after),
            "X-RateLimit-Limit": str(exc.limit),
            "X-RateLimit-Remaining": "0",
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    errors = [
        {"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]}
        for e in exc.errors()
    ]
    return problem(422, "validation-error", "Invalid input", "The request has invalid fields", request, errors)


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    titles = {404: "Not found", 405: "Method not allowed"}
    return problem(exc.status_code, f"http-{exc.status_code}", titles.get(exc.status_code, "HTTP error"),
                   str(exc.detail), request, headers=getattr(exc, "headers", None))


# ── Middleware: request id, metrics, logs, faults ─────────────────────────


def route_template(request: Request) -> str:
    """Map a request to its route template so IDs don't explode label cardinality."""
    for route in app.routes:
        if hasattr(route, "path") and hasattr(route, "matches"):
            match, _ = route.matches(request.scope)
            if match == match.FULL:
                return route.path
    return "unmatched"


@app.middleware("http")
async def observe_requests(request: Request, call_next):
    global request_counter
    incoming = request.headers.get("X-Request-ID", "")
    request_id = incoming if REQUEST_ID_RE.match(incoming) else str(uuid.uuid4())
    request_id_var.set(request_id)
    transport_var.set("http")
    request.state.request_id = request_id

    method = request.method
    endpoint = route_template(request)
    start = time.perf_counter()
    in_flight = http_requests_in_flight.labels(method=method, endpoint=endpoint)
    in_flight.inc()
    try:
        fault = None
        if not request.url.path.startswith(NO_FAULT_PATHS):
            fault = faults.current_fault()
            if FAULT_INJECTION:
                request_counter += 1
                if request_counter % 5 == 0:
                    await asyncio.sleep(0.5)
                    logger.warning("fault_injected", extra={"fault": "latency", "delay_ms": 500})
                if request_counter % 7 == 0:
                    fault = "error"

        if fault == "error":
            logger.warning("fault_injected", extra={"fault": "error", "status": 503})
            response = problem(503, "service-unavailable", "Service unavailable",
                               "Injected server failure", request)
        else:
            try:
                response = await call_next(request)
            except Exception:
                logger.exception("unhandled_error")
                response = problem(500, "internal-error", "Internal server error",
                                   "Unexpected error; see logs with this request_id", request)
            if fault == "slow":
                logger.warning("fault_injected", extra={"fault": "slow", "delay_ms": faults.SLOW_DELAY_S * 1000})
                await asyncio.sleep(faults.SLOW_DELAY_S)
    finally:
        in_flight.dec()

    duration = time.perf_counter() - start
    if endpoint != "/metrics":
        http_requests_total.labels(method=method, endpoint=endpoint, status=str(response.status_code)).inc()
        http_request_duration_seconds.labels(method=method, endpoint=endpoint).observe(duration)

    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time-Ms"] = f"{duration * 1000:.2f}"

    logger.info(
        "request",
        extra={
            "request_id": request_id,
            "method": method,
            "endpoint": endpoint,
            "status": response.status_code,
            "duration_ms": round(duration * 1000, 2),
        },
    )
    return response


# ── OpenAPI helpers ───────────────────────────────────────────────────────


def errors_doc(*codes: int) -> dict:
    descriptions = {
        404: "Resource not found",
        409: "Conflict with the current state (empty cart, not enough stock, bad status change)",
        422: "Invalid input (body, path, query or header)",
        429: "Rate limit exceeded; see Retry-After",
        503: "Service unavailable (dependency down or injected fault)",
    }
    out = {}
    for code in codes:
        out[code] = {
            "description": descriptions[code],
            "content": {"application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}},
        }
    if 429 in codes:
        out[429]["headers"] = {"Retry-After": {"description": "Seconds until calls are accepted again", "schema": {"type": "integer"}}}
    return out


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
    problem_schema = Problem.model_json_schema(ref_template="#/components/schemas/{model}")
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    components.update(problem_schema.pop("$defs", {}))
    components["Problem"] = problem_schema
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi

CART_ID_PATH = {"pattern": r"^[A-Za-z0-9_-]{1,64}$", "examples": ["cart-42"]}


# ── Health ────────────────────────────────────────────────────────────────


@app.get("/health", tags=["ops"])
def health():
    return {"status": "ok"}


# ── Products ──────────────────────────────────────────────────────────────


@app.get("/products", tags=["products"], response_model=list[Product], responses=errors_doc(422))
def list_products(category: str | None = None):
    return svc.list_products(category)


@app.get("/products/{product_id}", tags=["products"], response_model=Product, responses=errors_doc(404))
def get_product(product_id: str):
    return svc.get_product(product_id)


# ── Carts ─────────────────────────────────────────────────────────────────


@app.get("/carts/{cart_id}", tags=["carts"], response_model=Cart, responses=errors_doc(422))
def get_cart(cart_id: str):
    """Read a cart. An unknown cart id returns an empty cart and does not create anything."""
    return svc.get_cart(cart_id)


@app.put("/carts/{cart_id}/items/{product_id}", tags=["carts"], response_model=Cart,
         responses=errors_doc(404, 409, 422))
def set_cart_item(cart_id: str, product_id: str, item: SetCartItem):
    """Set the quantity of a product in the cart. Idempotent: repeating it leaves the same cart."""
    return svc.set_cart_item(cart_id, product_id, item.quantity)


@app.delete("/carts/{cart_id}/items/{product_id}", tags=["carts"], status_code=204,
            responses=errors_doc(422))
def remove_cart_item(cart_id: str, product_id: str):
    """Remove a product from the cart. Idempotent: removing a missing item also returns 204."""
    svc.remove_cart_item(cart_id, product_id)
    return Response(status_code=204)


# ── Orders ────────────────────────────────────────────────────────────────


def limit_orders(request: Request) -> int:
    try:
        return orders_limiter.hit(request.client.host if request.client else "unknown")
    except RateLimited:
        http_rate_limited_total.labels(endpoint="/orders").inc()
        logger.warning("rate_limited", extra={"endpoint": "/orders"})
        raise


@app.post(
    "/orders", tags=["orders"], status_code=201, response_model=Order,
    responses={
        **errors_doc(404, 409, 422, 429, 503),
        201: {
            "description": "Order created, or the original order replayed for a repeated Idempotency-Key",
            "headers": {
                "Location": {"description": "URL of the order", "schema": {"type": "string"}},
                "Idempotent-Replayed": {"description": "`true` when this is a replay", "schema": {"type": "string"}},
                "X-RateLimit-Remaining": {"description": "Calls left in the current window", "schema": {"type": "integer"}},
            },
        },
    },
)
def place_order(
    body: PlaceOrderRequest,
    response: Response,
    remaining: Annotated[int, Depends(limit_orders)],
    idempotency_key: Annotated[str | None, Header(
        alias="Idempotency-Key", min_length=1, max_length=128,
        description="Unique per checkout attempt (e.g. a UUID). Retries with the same key never create a second order.",
    )] = None,
):
    """Place an order from a cart (rate limited per client IP)."""
    order, replayed = svc.place_order(body.cart_id, idempotency_key, transport="http")
    response.headers["Location"] = f"/orders/{order.id}"
    response.headers["X-RateLimit-Limit"] = str(ORDERS_RATE_LIMIT)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    if replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return order


@app.get("/orders", tags=["orders"], response_model=list[Order], responses=errors_doc(422))
def list_orders(status: OrderStatus | None = None):
    return svc.list_orders(status)


@app.get("/orders/{order_id}", tags=["orders"], response_model=Order, responses=errors_doc(404))
def get_order(order_id: str):
    return svc.get_order(order_id)


@app.patch("/orders/{order_id}", tags=["orders"], response_model=Order, responses=errors_doc(404, 409, 422))
def update_order_status(order_id: str, body: UpdateOrderStatus):
    """Move an order along PENDING → CONFIRMED → SHIPPED → DELIVERED, or CANCELLED before delivery.
    Asking for the status it already has returns the order unchanged."""
    return svc.change_order_status(order_id, body.status)


# ── Stats ─────────────────────────────────────────────────────────────────


@app.get("/stats", tags=["ops"])
def get_stats():
    return svc.get_stats()


# ── Fault switch for the reliability demo ────────────────────────────────


class FaultRequest(BaseModel):
    mode: str = Field(pattern="^(error|slow|off)$", examples=["error"])
    duration_s: float = Field(default=10, gt=0, le=300)


@app.post("/admin/faults", tags=["testing"], responses=errors_doc(404, 422))
def set_fault(body: FaultRequest):
    """Turn on a temporary fault (needs ENABLE_FAULT_ADMIN=true). `error` returns 503;
    `slow` runs the request then delays the response past the client's timeout."""
    if not faults.ADMIN_ENABLED:
        raise StarletteHTTPException(404, "Not Found")
    faults.set_fault(body.mode, body.duration_s)
    logger.warning("fault_switched", extra={"fault": body.mode, "duration_s": body.duration_s})
    return {"mode": body.mode, "expires_in_ms": faults.remaining_ms()}


# ── Cardinality Demo (A1 Part E.2) ────────────────────────────────────────


@app.post("/demo/cardinality", tags=["demo"])
def cardinality_demo(mode: str = "good", count: int = 100):
    """
    mode=good: increments a single counter (1 time series)
    mode=bad: increments a counter with a unique request_id label per call (N time series)
    """
    for _ in range(count):
        if mode == "bad":
            demo_bad.labels(request_id=str(uuid.uuid4())).inc()
        else:
            demo_good.inc()
    return {
        "mode": mode,
        "increments": count,
        "explanation": f"{'Each increment created a new time series (cardinality explosion!)' if mode == 'bad' else 'All increments went to a single time series (correct)'}",
    }


# ── Metrics ───────────────────────────────────────────────────────────────


@app.get("/metrics", tags=["ops"])
def get_metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
