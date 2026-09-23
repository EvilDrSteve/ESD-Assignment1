from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import time
import uuid
import os

from models import CartItem, OrderStatus
from store import store

from fastapi.responses import Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, Counter as PromCounter

from metrics import *
from logger import logger

demo_good = PromCounter("demo_requests_total_good", "Demo counter without high-cardinality label")
demo_bad = PromCounter("demo_requests_total_bad", "Demo counter with high-cardinality label", ["request_id"])

FAULT_INJECTION = os.environ.get("FAULT_INJECTION", "false").lower() == "true"
request_counter = 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Online Store API starting up")
    yield
    print("Online Store API shutting down")


app = FastAPI(
    title="Online Store API",
    description="A simple online store — demo app for ESD Assignment 1.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    global request_counter
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    startTime = time.perf_counter()

    if FAULT_INJECTION:
        request_counter += 1
        if request_counter % 5 == 0:
            time.sleep(0.5)
            logger.warning("fault_injected", extra={
                "request_id": request_id,
                "fault": "latency",
                "delay_ms": 500,
            })
        if request_counter % 7 == 0:
            duration = time.perf_counter() - startTime
            endpoint = request.url.path
            for route in app.routes:
                if hasattr(route, "path") and hasattr(route, "matches"):
                    match, _ = route.matches(request.scope)
                    if match == match.FULL:
                        endpoint = route.path
                        break
            http_requests_total.labels(method=request.method, endpoint=endpoint, status="500").inc()
            http_request_duration_seconds.labels(method=request.method, endpoint=endpoint).observe(duration)
            logger.warning("fault_injected", extra={
                "request_id": request_id,
                "fault": "error",
                "status": 500,
            })
            return JSONResponse(status_code=500, content={"detail": "Injected server error"})

    response = await call_next(request)

    duration = time.perf_counter() - startTime
    method = request.method

    # Match the request to a route template so dynamic IDs don't explode cardinality
    endpoint = request.url.path
    for route in app.routes:
        if hasattr(route, "path") and hasattr(route, "matches"):
            match, _ = route.matches(request.scope)
            if match == match.FULL:
                endpoint = route.path
                break

    if endpoint != "/metrics":
        http_requests_total.labels(
            method=method, endpoint=endpoint, status=str(response.status_code)
        ).inc()
        http_request_duration_seconds.labels(method=method, endpoint=endpoint).observe(
            duration
        )

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


# ── Health ────────────────────────────────────────────────────────────────


@app.get("/health", tags=["ops"])
def health():
    return {"status": "ok"}


# ── Products ──────────────────────────────────────────────────────────────


@app.get("/products", tags=["products"])
def list_products(category: str | None = None):
    return store.list_products(category)


@app.get("/products/{product_id}", tags=["products"])
def get_product(product_id: str, request: Request):
    product = store.get_product(product_id)
    if not product:
        logger.warning(
            "product_not_found",
            extra={
                "request_id": request.state.request_id,
                "product_id": product_id
            },
        )
        raise HTTPException(status_code=404, detail="Product not found")
    return product


# ── Cart ──────────────────────────────────────────────────────────────────


@app.get("/cart/{cart_id}", tags=["cart"])
def get_cart(cart_id: str):
    return store.get_cart(cart_id)


@app.post("/cart/{cart_id}/items", tags=["cart"])
def add_to_cart(cart_id: str, item: CartItem, request: Request):
    product = store.get_product(item.product_id)
    if not product:
        logger.warning(
            "product_not_found",
            extra={
                "request_id": request.state.request_id,
                "product_id": item.product_id
            },
        )
        raise HTTPException(status_code=404, detail="Product not found")
    if item.quantity < 1:
        raise HTTPException(status_code=400, detail="Quantity must be at least 1")
    if item.quantity > product.stock:
        logger.warning(
            "stock_empty",
            extra={
                "request_id": request.state.request_id,
                "product_id": product.id
            },
        )
        raise HTTPException(status_code=409, detail="Not enough stock")

    cart = store.add_to_cart(cart_id, item)
    active_carts.set(sum(1 for c in store._carts.values() if c))
    
    logger.info(
        "item_added_to_cart",
        extra={
            "request_id": request.state.request_id,
            "cart_id": cart.id,
            "product_id": item.product_id
        },
    )
    return cart


@app.delete("/cart/{cart_id}/items/{product_id}", tags=["cart"])
def remove_from_cart(cart_id: str, product_id: str):
    cart = store.remove_from_cart(cart_id, product_id)
    active_carts.set(sum(1 for c in store._carts.values() if c))
    return cart


# ── Orders ────────────────────────────────────────────────────────────────


@app.post("/orders", tags=["orders"], status_code=201)
def place_order(cart_id: str, request: Request):
    order = store.create_order(cart_id)

    if not order:
        logger.warning(
            "empty_cart_or_insufficient_stock",
            extra={
                "request_id": request.state.request_id,
                "cart_id": cart_id
            },
        )
        raise HTTPException(
            status_code=400, detail="Cart is empty or insufficient stock"
        )

    orders_placed_total.inc()
    orders_pending.inc()
    order_value_dollars.observe(order.total)
    active_carts.set(sum(1 for c in store._carts.values() if c))

    logger.info(
        "order_placed",
        extra={
            "request_id": request.state.request_id,
            "order_id": order.id,
            "total": order.total
        },
    )
    return order


@app.get("/orders", tags=["orders"])
def list_orders(status: OrderStatus | None = None):
    return store.list_orders(status)


@app.get("/orders/{order_id}", tags=["orders"])
def get_order(order_id: str, request: Request):
    order = store.get_order(order_id)
    if not order:
        logger.warning(
            "order_not_found",
            extra={
                "request_id": request.state.request_id,
                "order_id": order_id
            },
        )
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@app.put("/orders/{order_id}/confirm", tags=["orders"])
def confirm_order(order_id: str, request: Request):
    order = store.get_order(order_id)
    if not order:
        logger.warning(
            "order_not_found",
            extra={
                "request_id": request.state.request_id,
                "order_id": order_id
            },
        )
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != OrderStatus.PENDING:
        logger.warning(
            "status_update_failed",
            extra={
                "request_id": request.state.request_id,
                "order_id": order_id
            },
        )
        raise HTTPException(
            status_code=409, detail=f"Cannot confirm order with status '{order.status}'"
        )

    order = store.update_order_status(order_id, OrderStatus.CONFIRMED)
    orders_pending.dec()
    return order


@app.put("/orders/{order_id}/ship", tags=["orders"])
def ship_order(order_id: str, request: Request):
    order = store.get_order(order_id)
    if not order:
        logger.warning(
            "order_not_found",
            extra={
                "request_id": request.state.request_id,
                "order_id": order_id
            },
        )
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != OrderStatus.CONFIRMED:
        logger.warning(
            "status_update_failed",
            extra={
                "request_id": request.state.request_id,
                "order_id": order_id
            },
        )
        raise HTTPException(
            status_code=409, detail=f"Cannot ship order with status '{order.status}'"
        )
    return store.update_order_status(order_id, OrderStatus.SHIPPED)


@app.put("/orders/{order_id}/deliver", tags=["orders"])
def deliver_order(order_id: str, request: Request):
    order = store.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != OrderStatus.SHIPPED:
        logger.warning(
            "status_update_failed",
            extra={
                "request_id": request.state.request_id,
                "order_id": order_id
            },
        )
        raise HTTPException(
            status_code=409, detail=f"Cannot deliver order with status '{order.status}'"
        )

    return store.update_order_status(order_id, OrderStatus.DELIVERED)


@app.put("/orders/{order_id}/cancel", tags=["orders"])
def cancel_order(order_id: str, request: Request):
    order = store.get_order(order_id)
    if not order:
        logger.warning(
            "order_not_found",
            extra={
                "request_id": request.state.request_id,
                "order_id": order_id
            },
        )
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status in (OrderStatus.DELIVERED, OrderStatus.CANCELLED):
        logger.warning(
            "status_update_failed",
            extra={
                "request_id": request.state.request_id,
                "order_id": order_id
            },
        )
        raise HTTPException(
            status_code=409, detail=f"Cannot cancel order with status '{order.status}'"
        )

    if order.status == OrderStatus.PENDING:
        orders_pending.dec()
    order = store.cancel_order(order_id)
    orders_cancelled_total.inc()
    
    logger.info(
        "order_cancelled",
        extra={
            "request_id": request.state.request_id,
            "order_id": order.id,
        },
    )
    return order


# ── Stats ─────────────────────────────────────────────────────────────────


@app.get("/stats", tags=["ops"])
def get_stats():
    return store.get_stats()


# ── Cardinality Demo (Part E.2) ──────────────────────────────────────────


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


# ── Metrics ─────────────────────────────────────────────────────────────────


@app.get("/metrics", tags=["ops"])
def get_metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
