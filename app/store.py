"""Redis-backed store: the single source of truth for the HTTP and gRPC services.

Key layout
  product:{id}            hash   name, description, category, price, stock
  products                set    all product ids
  cart:{id}               hash   product_id -> quantity (deleted when empty)
  carts:active            set    cart ids that currently hold items
  order:{id}              string Order JSON
  orders                  zset   order ids scored by creation time
  orders:status:{status}  set    order ids per status (for cheap counts)
  idem:{scope}:{key}      string idempotency record JSON, with a TTL
"""

import json
import os
from datetime import datetime, timezone
from typing import Callable, Optional
import uuid

import redis

from models import Product, Cart, CartItemResponse, Order, OrderItem, OrderStatus


SEED_PRODUCTS = [
    Product(id="p1", name="Wireless Mouse", description="Ergonomic wireless mouse", category="electronics", price=29.99, stock=999999),
    Product(id="p2", name="Mechanical Keyboard", description="RGB mechanical keyboard", category="electronics", price=89.99, stock=999999),
    Product(id="p3", name="USB-C Hub", description="7-in-1 USB-C hub", category="electronics", price=45.99, stock=999999),
    Product(id="p4", name="Notebook", description="200-page ruled notebook", category="stationery", price=5.99, stock=999999),
    Product(id="p5", name="Ballpoint Pens (10pk)", description="Pack of 10 ballpoint pens", category="stationery", price=8.99, stock=999999),
    Product(id="p6", name="Desk Lamp", description="LED desk lamp with dimmer", category="furniture", price=34.99, stock=999999),
    Product(id="p7", name="Monitor Stand", description="Adjustable monitor riser", category="furniture", price=49.99, stock=999999),
    Product(id="p8", name="Webcam HD", description="1080p webcam with mic", category="electronics", price=59.99, stock=999999),
    # Low stock on purpose, so "insufficient stock" is easy to demonstrate
    Product(id="p9", name="Limited Edition Mug", description="Only a few made", category="kitchen", price=14.99, stock=3),
]


def _product_key(pid: str) -> str:
    return f"product:{pid}"


def _cart_key(cart_id: str) -> str:
    return f"cart:{cart_id}"


def _order_key(order_id: str) -> str:
    return f"order:{order_id}"


def _status_key(status: str) -> str:
    return f"orders:status:{status}"


class RedisStore:
    def __init__(self, client: redis.Redis):
        self.r = client

    def seed(self):
        """Load the catalogue once; later restarts of the app keep existing stock."""
        if not self.r.set("store:seeded", "1", nx=True):
            return
        pipe = self.r.pipeline()
        for p in SEED_PRODUCTS:
            pipe.hset(_product_key(p.id), mapping=p.model_dump(exclude={"id"}))
            pipe.sadd("products", p.id)
        pipe.execute()

    # ── Products ──────────────────────────────────────────────────────────

    def _load_product(self, pid: str, data: dict) -> Optional[Product]:
        if not data:
            return None
        return Product(id=pid, **data)

    def get_product(self, product_id: str) -> Optional[Product]:
        return self._load_product(product_id, self.r.hgetall(_product_key(product_id)))

    def list_products(self, category: Optional[str] = None) -> list[Product]:
        ids = sorted(self.r.smembers("products"))
        products = [self.get_product(pid) for pid in ids]
        products = [p for p in products if p]
        if category:
            products = [p for p in products if p.category == category]
        return products

    # ── Cart ──────────────────────────────────────────────────────────────

    def cart_exists(self, cart_id: str) -> bool:
        return bool(self.r.exists(_cart_key(cart_id)))

    def get_cart(self, cart_id: str) -> Cart:
        """Read-only: an unknown cart is returned as empty and is NOT created."""
        raw = self.r.hgetall(_cart_key(cart_id))
        items = []
        total = 0.0
        for pid, qty in sorted(raw.items()):
            product = self.get_product(pid)
            if product:
                subtotal = round(product.price * int(qty), 2)
                items.append(CartItemResponse(
                    product_id=pid, name=product.name,
                    price=product.price, quantity=int(qty), subtotal=subtotal,
                ))
                total += subtotal
        return Cart(id=cart_id, items=items, total=round(total, 2))

    def set_cart_item(self, cart_id: str, product_id: str, quantity: int):
        pipe = self.r.pipeline()
        pipe.hset(_cart_key(cart_id), product_id, quantity)
        pipe.sadd("carts:active", cart_id)
        pipe.execute()

    def remove_cart_item(self, cart_id: str, product_id: str) -> bool:
        removed = self.r.hdel(_cart_key(cart_id), product_id)
        if not self.r.exists(_cart_key(cart_id)):
            self.r.srem("carts:active", cart_id)
        return bool(removed)

    def count_active_carts(self) -> int:
        return self.r.scard("carts:active")

    # ── Orders ────────────────────────────────────────────────────────────

    def checkout(self, cart_id: str, build_items: Callable[[dict, dict], list[OrderItem]]) -> Order:
        """Atomically turn a cart into an order and deduct stock.

        `build_items(cart, products)` applies the business rules and raises a
        DomainError to abort. Redis WATCH retries the whole thing if the cart or
        any product changes underneath us, so stock can never go negative.
        """
        cart_key = _cart_key(cart_id)

        def txn(pipe: redis.client.Pipeline):
            cart = {pid: int(q) for pid, q in pipe.hgetall(cart_key).items()}
            product_keys = [_product_key(pid) for pid in cart]
            if product_keys:
                pipe.watch(*product_keys)
            products = {}
            for pid in cart:
                p = self._load_product(pid, pipe.hgetall(_product_key(pid)))
                if p:
                    products[pid] = p

            items = build_items(cart, products)  # may raise DomainError

            now = datetime.now(timezone.utc)
            order = Order(
                id=str(uuid.uuid4()),
                cart_id=cart_id,
                items=items,
                total=round(sum(i.subtotal for i in items), 2),
                status=OrderStatus.PENDING,
                created_at=now,
                updated_at=now,
            )
            pipe.multi()
            for item in items:
                pipe.hincrby(_product_key(item.product_id), "stock", -item.quantity)
            pipe.set(_order_key(order.id), order.model_dump_json())
            pipe.zadd("orders", {order.id: now.timestamp()})
            pipe.sadd(_status_key(order.status), order.id)
            pipe.delete(cart_key)
            pipe.srem("carts:active", cart_id)
            return order

        return self.r.transaction(txn, cart_key, value_from_callable=True)

    def get_order(self, order_id: str) -> Optional[Order]:
        raw = self.r.get(_order_key(order_id))
        return Order.model_validate_json(raw) if raw else None

    def list_orders(self, status: Optional[OrderStatus] = None) -> list[Order]:
        ids = self.r.zrange("orders", 0, -1)
        orders = [self.get_order(oid) for oid in ids]
        orders = [o for o in orders if o]
        if status:
            orders = [o for o in orders if o.status == status]
        return orders

    def change_status(self, order_id: str, decide: Callable[[Order], Optional[OrderStatus]]) -> Order:
        """Atomically move an order to the status returned by `decide(order)`.

        `decide` applies the state machine; returning None means "no change"
        (e.g. a repeated request). Cancelling restores stock.
        """
        key = _order_key(order_id)

        def txn(pipe: redis.client.Pipeline):
            raw = pipe.get(key)
            if not raw:
                return None
            order = Order.model_validate_json(raw)
            new_status = decide(order)
            if new_status is None:
                return order
            updated = order.model_copy(update={"status": new_status.value, "updated_at": datetime.now(timezone.utc)})
            pipe.multi()
            pipe.set(key, updated.model_dump_json())
            pipe.smove(_status_key(order.status), _status_key(updated.status), order_id)
            if new_status == OrderStatus.CANCELLED:
                for item in order.items:
                    pipe.hincrby(_product_key(item.product_id), "stock", item.quantity)
            return updated

        return self.r.transaction(txn, key, value_from_callable=True)

    def count_orders(self, status: OrderStatus) -> int:
        return self.r.scard(_status_key(status.value))

    def get_stats(self) -> dict:
        order_counts = {s.value: self.count_orders(s) for s in OrderStatus}
        total_revenue = sum(
            o.total for o in self.list_orders() if o.status != OrderStatus.CANCELLED
        )
        return {
            "orders": order_counts,
            "total_revenue": round(total_revenue, 2),
            "products_in_catalog": self.r.scard("products"),
            "active_carts": self.count_active_carts(),
        }

    # ── Idempotency records ───────────────────────────────────────────────

    def claim_idempotency_key(self, key: str, record: dict, ttl_s: int) -> bool:
        return bool(self.r.set(key, json.dumps(record), nx=True, ex=ttl_s))

    def get_idempotency_record(self, key: str) -> Optional[dict]:
        raw = self.r.get(key)
        return json.loads(raw) if raw else None

    def save_idempotency_record(self, key: str, record: dict, ttl_s: int):
        self.r.set(key, json.dumps(record), ex=ttl_s)

    def release_idempotency_key(self, key: str):
        self.r.delete(key)


def connect() -> redis.Redis:
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2)


store = RedisStore(connect())
