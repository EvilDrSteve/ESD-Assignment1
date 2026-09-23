from pydantic import BaseModel
from enum import Enum
from datetime import datetime
from typing import Optional


class OrderStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class Product(BaseModel):
    id: str
    name: str
    description: str
    category: str
    price: float
    stock: int


class CartItem(BaseModel):
    product_id: str
    quantity: int = 1


class CartItemResponse(BaseModel):
    product_id: str
    name: str
    price: float
    quantity: int
    subtotal: float


class Cart(BaseModel):
    id: str
    items: list[CartItemResponse]
    total: float


class OrderItem(BaseModel):
    product_id: str
    name: str
    price: float
    quantity: int
    subtotal: float


class Order(BaseModel):
    id: str
    items: list[OrderItem]
    total: float
    status: OrderStatus
    created_at: datetime
    updated_at: datetime

    class Config:
        use_enum_values = True
