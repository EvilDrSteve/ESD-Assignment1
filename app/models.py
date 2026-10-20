from pydantic import BaseModel, ConfigDict, Field
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


class SetCartItem(BaseModel):
    """Body for PUT /carts/{cart_id}/items/{product_id}: sets (not adds) the quantity."""

    model_config = ConfigDict(extra="forbid")

    quantity: int = Field(ge=1, le=100, examples=[2])


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
    model_config = ConfigDict(use_enum_values=True)

    id: str
    cart_id: str
    items: list[OrderItem]
    total: float
    status: OrderStatus
    created_at: datetime
    updated_at: datetime


class PlaceOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cart_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$", examples=["cart-42"])


class UpdateOrderStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: OrderStatus = Field(examples=["confirmed"])


class FieldError(BaseModel):
    field: str
    message: str


class Problem(BaseModel):
    """RFC 9457 problem details, used for every error response."""

    type: str = Field(examples=["/problems/insufficient-stock"])
    title: str = Field(examples=["Not enough stock"])
    status: int = Field(examples=[409])
    detail: str = Field(examples=["Only 3 of 'p1' left"])
    instance: str = Field(examples=["/orders"])
    request_id: Optional[str] = None
    errors: Optional[list[FieldError]] = None
