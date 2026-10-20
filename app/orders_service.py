"""Business rules for carts and orders.

Both front ends call these functions: the FastAPI routes in main.py and the
gRPC handler in grpc_server.py. Neither front end touches Redis directly, so
the rules (validation, stock checks, state machine, idempotency) exist once.
"""

import hashlib
import re
from typing import Optional

from errors import (
    DomainError, NotFound, InvalidInput, EmptyCart, InsufficientStock,
    InvalidTransition, IdempotencyKeyReused, IdempotencyInProgress,
)
from logger import logger
from metrics import (
    orders_placed_total, orders_cancelled_total, checkout_failures_total,
    idempotent_replays_total, order_value_dollars,
)
from models import Cart, Order, OrderItem, OrderStatus, Product
from store import store

CART_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
IDEMPOTENCY_TTL_S = 24 * 60 * 60
MAX_QUANTITY = 100

# Allowed status changes. Cancelling is possible until the order is delivered.
TRANSITIONS = {
    OrderStatus.PENDING: {OrderStatus.CONFIRMED, OrderStatus.CANCELLED},
    OrderStatus.CONFIRMED: {OrderStatus.SHIPPED, OrderStatus.CANCELLED},
    OrderStatus.SHIPPED: {OrderStatus.DELIVERED, OrderStatus.CANCELLED},
    OrderStatus.DELIVERED: set(),
    OrderStatus.CANCELLED: set(),
}


# ── Validation ────────────────────────────────────────────────────────────


def validate_cart_id(cart_id: str):
    if not CART_ID_RE.match(cart_id or ""):
        raise InvalidInput("cart_id must be 1-64 characters: letters, digits, '-' or '_'")


def validate_idempotency_key(key: str):
    if not IDEMPOTENCY_KEY_RE.match(key):
        raise InvalidInput("Idempotency key must be 1-128 characters: letters, digits, '-', '_', '.', ':'")


# ── Products ──────────────────────────────────────────────────────────────


def list_products(category: Optional[str] = None) -> list[Product]:
    return store.list_products(category)


def get_product(product_id: str) -> Product:
    product = store.get_product(product_id)
    if not product:
        logger.warning("product_not_found", extra={"product_id": product_id})
        raise NotFound(f"Product '{product_id}' does not exist")
    return product


# ── Cart ──────────────────────────────────────────────────────────────────


def get_cart(cart_id: str) -> Cart:
    validate_cart_id(cart_id)
    return store.get_cart(cart_id)


def set_cart_item(cart_id: str, product_id: str, quantity: int) -> Cart:
    """Set the quantity of one product in a cart. Repeating it gives the same cart."""
    validate_cart_id(cart_id)
    if not 1 <= quantity <= MAX_QUANTITY:
        raise InvalidInput(f"quantity must be between 1 and {MAX_QUANTITY}")
    product = get_product(product_id)
    if quantity > product.stock:
        logger.warning("stock_empty", extra={"product_id": product_id})
        raise InsufficientStock(f"Only {product.stock} of '{product_id}' left")

    store.set_cart_item(cart_id, product_id, quantity)
    logger.info("item_set_in_cart", extra={"cart_id": cart_id, "product_id": product_id, "quantity": quantity})
    return store.get_cart(cart_id)


def remove_cart_item(cart_id: str, product_id: str):
    validate_cart_id(cart_id)
    store.remove_cart_item(cart_id, product_id)


# ── Orders ────────────────────────────────────────────────────────────────


def _build_order_items(cart: dict[str, int], products: dict[str, Product]) -> list[OrderItem]:
    if not cart:
        raise EmptyCart("Cart is empty or does not exist; add items before placing an order")
    items = []
    for pid, qty in sorted(cart.items()):
        product = products.get(pid)
        if not product:
            raise NotFound(f"Product '{pid}' in the cart no longer exists")
        if product.stock < qty:
            raise InsufficientStock(f"Only {product.stock} of '{pid}' left, cart wants {qty}")
        items.append(OrderItem(
            product_id=pid, name=product.name, price=product.price,
            quantity=qty, subtotal=round(product.price * qty, 2),
        ))
    return items


def _fingerprint(cart_id: str) -> str:
    return hashlib.sha256(f"place_order|{cart_id}".encode()).hexdigest()


def place_order(cart_id: str, idempotency_key: Optional[str], transport: str) -> tuple[Order, bool]:
    """Turn a cart into a pending order.

    Returns (order, replayed). With an idempotency key, a repeat of a request
    that already succeeded returns the original order (replayed=True) instead
    of creating a second one. Same key + different cart -> IdempotencyKeyReused.
    """
    try:
        validate_cart_id(cart_id)
        if idempotency_key is None:
            return _create_order(cart_id, transport), False

        validate_idempotency_key(idempotency_key)
        record_key = f"idem:place_order:{idempotency_key}"
        fingerprint = _fingerprint(cart_id)

        if not store.claim_idempotency_key(record_key, {"fp": fingerprint, "order_id": None}, IDEMPOTENCY_TTL_S):
            record = store.get_idempotency_record(record_key)
            if record is None:  # expired between the two calls; treat as new
                return place_order(cart_id, idempotency_key, transport)
            if record["fp"] != fingerprint:
                raise IdempotencyKeyReused("This Idempotency-Key was already used for a different cart")
            if not record["order_id"]:
                raise IdempotencyInProgress("The first request with this key is still running; retry shortly")
            order = store.get_order(record["order_id"])
            idempotent_replays_total.labels(transport=transport).inc()
            logger.info("order_replayed", extra={"order_id": order.id, "cart_id": cart_id})
            return order, True

        try:
            order = _create_order(cart_id, transport)
        except Exception:
            # Business rule failed: free the key so the client can fix the cart and retry with it
            store.release_idempotency_key(record_key)
            raise
        store.save_idempotency_record(record_key, {"fp": fingerprint, "order_id": order.id}, IDEMPOTENCY_TTL_S)
        return order, False

    except DomainError as e:
        checkout_failures_total.labels(transport=transport, reason=e.type).inc()
        logger.warning("order_rejected", extra={"cart_id": cart_id, "reason": e.type})
        raise


def _create_order(cart_id: str, transport: str) -> Order:
    order = store.checkout(cart_id, _build_order_items)
    orders_placed_total.labels(transport=transport).inc()
    order_value_dollars.observe(order.total)
    logger.info("order_placed", extra={"order_id": order.id, "cart_id": cart_id, "total": order.total})
    return order


def get_order(order_id: str) -> Order:
    order = store.get_order(order_id)
    if not order:
        logger.warning("order_not_found", extra={"order_id": order_id})
        raise NotFound(f"Order '{order_id}' does not exist")
    return order


def list_orders(status: Optional[OrderStatus] = None) -> list[Order]:
    return store.list_orders(status)


def change_order_status(order_id: str, target: OrderStatus) -> Order:
    """Move an order along the state machine.

    Asking for the status the order already has is a no-op that returns the
    order, so a retried "confirm" or "cancel" is safe.
    """
    changed = {"value": False}

    def decide(order: Order) -> Optional[OrderStatus]:
        changed["value"] = False  # decide() re-runs if Redis retries the transaction
        current = OrderStatus(order.status)
        if current == target:
            return None
        if target not in TRANSITIONS[current]:
            raise InvalidTransition(f"Cannot change order from '{current.value}' to '{target.value}'")
        changed["value"] = True
        return target

    try:
        order = store.change_status(order_id, decide)
    except InvalidTransition:
        logger.warning("status_update_failed", extra={"order_id": order_id, "target": target.value})
        raise
    if order is None:
        logger.warning("order_not_found", extra={"order_id": order_id})
        raise NotFound(f"Order '{order_id}' does not exist")

    if changed["value"]:
        if target == OrderStatus.CANCELLED:
            orders_cancelled_total.inc()
            logger.info("order_cancelled", extra={"order_id": order_id})
        else:
            logger.info("order_status_changed", extra={"order_id": order_id, "status": target.value})
    return order


def get_stats() -> dict:
    return store.get_stats()
