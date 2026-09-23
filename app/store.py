from datetime import datetime, timezone
from typing import Dict, Optional
import uuid

from models import Product, Cart, CartItem, CartItemResponse, Order, OrderItem, OrderStatus


SEED_PRODUCTS = [
    Product(id="p1", name="Wireless Mouse", description="Ergonomic wireless mouse", category="electronics", price=29.99, stock=999999),
    Product(id="p2", name="Mechanical Keyboard", description="RGB mechanical keyboard", category="electronics", price=89.99, stock=999999),
    Product(id="p3", name="USB-C Hub", description="7-in-1 USB-C hub", category="electronics", price=45.99, stock=999999),
    Product(id="p4", name="Notebook", description="200-page ruled notebook", category="stationery", price=5.99, stock=999999),
    Product(id="p5", name="Ballpoint Pens (10pk)", description="Pack of 10 ballpoint pens", category="stationery", price=8.99, stock=999999),
    Product(id="p6", name="Desk Lamp", description="LED desk lamp with dimmer", category="furniture", price=34.99, stock=999999),
    Product(id="p7", name="Monitor Stand", description="Adjustable monitor riser", category="furniture", price=49.99, stock=999999),
    Product(id="p8", name="Webcam HD", description="1080p webcam with mic", category="electronics", price=59.99, stock=999999),
]


class InMemoryStore:
    def __init__(self):
        self._products: Dict[str, Product] = {p.id: p for p in SEED_PRODUCTS}
        self._carts: Dict[str, Dict[str, int]] = {}  # cart_id -> {product_id: qty}
        self._orders: Dict[str, Order] = {}

    # ── Products ──────────────────────────────────────────────────────────

    def list_products(self, category: Optional[str] = None) -> list[Product]:
        products = list(self._products.values())
        if category:
            products = [p for p in products if p.category == category]
        return products

    def get_product(self, product_id: str) -> Optional[Product]:
        return self._products.get(product_id)

    # ── Cart ──────────────────────────────────────────────────────────────

    def get_or_create_cart(self, cart_id: str) -> str:
        if cart_id not in self._carts:
            self._carts[cart_id] = {}
        return cart_id

    def add_to_cart(self, cart_id: str, item: CartItem) -> Cart:
        self.get_or_create_cart(cart_id)
        current_qty = self._carts[cart_id].get(item.product_id, 0)
        self._carts[cart_id][item.product_id] = current_qty + item.quantity
        return self.get_cart(cart_id)

    def remove_from_cart(self, cart_id: str, product_id: str) -> Cart:
        if cart_id in self._carts:
            self._carts[cart_id].pop(product_id, None)
        return self.get_cart(cart_id)

    def get_cart(self, cart_id: str) -> Cart:
        self.get_or_create_cart(cart_id)
        items = []
        total = 0.0
        for pid, qty in self._carts[cart_id].items():
            product = self._products.get(pid)
            if product:
                subtotal = round(product.price * qty, 2)
                items.append(CartItemResponse(
                    product_id=pid, name=product.name,
                    price=product.price, quantity=qty, subtotal=subtotal,
                ))
                total += subtotal
        return Cart(id=cart_id, items=items, total=round(total, 2))

    def clear_cart(self, cart_id: str):
        self._carts.pop(cart_id, None)

    # ── Orders ────────────────────────────────────────────────────────────

    def create_order(self, cart_id: str) -> Optional[Order]:
        if cart_id not in self._carts or not self._carts[cart_id]:
            return None

        now = datetime.now(timezone.utc)
        order_items = []
        total = 0.0

        for pid, qty in self._carts[cart_id].items():
            product = self._products.get(pid)
            if not product or product.stock < qty:
                return None
            subtotal = round(product.price * qty, 2)
            order_items.append(OrderItem(
                product_id=pid, name=product.name,
                price=product.price, quantity=qty, subtotal=subtotal,
            ))
            total += subtotal

        # Deduct stock
        for item in order_items:
            p = self._products[item.product_id]
            self._products[item.product_id] = p.model_copy(
                update={"stock": p.stock - item.quantity}
            )

        order = Order(
            id=str(uuid.uuid4()),
            items=order_items,
            total=round(total, 2),
            status=OrderStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        self._orders[order.id] = order
        self.clear_cart(cart_id)
        return order

    def get_order(self, order_id: str) -> Optional[Order]:
        return self._orders.get(order_id)

    def list_orders(self, status: Optional[OrderStatus] = None) -> list[Order]:
        orders = list(self._orders.values())
        if status:
            orders = [o for o in orders if o.status == status]
        return orders

    def update_order_status(self, order_id: str, status: OrderStatus) -> Optional[Order]:
        order = self._orders.get(order_id)
        if not order:
            return None
        self._orders[order_id] = order.model_copy(
            update={"status": status, "updated_at": datetime.now(timezone.utc)}
        )
        return self._orders[order_id]

    def cancel_order(self, order_id: str) -> Optional[Order]:
        order = self._orders.get(order_id)
        if not order:
            return None
        # Restore stock
        for item in order.items:
            p = self._products.get(item.product_id)
            if p:
                self._products[item.product_id] = p.model_copy(
                    update={"stock": p.stock + item.quantity}
                )
        return self.update_order_status(order_id, OrderStatus.CANCELLED)

    def get_stats(self) -> dict:
        order_counts = {s.value: 0 for s in OrderStatus}
        total_revenue = 0.0
        for o in self._orders.values():
            order_counts[o.status] += 1
            if o.status not in (OrderStatus.CANCELLED,):
                total_revenue += o.total

        return {
            "orders": order_counts,
            "total_revenue": round(total_revenue, 2),
            "products_in_catalog": len(self._products),
            "active_carts": len(self._carts),
        }


store = InMemoryStore()
