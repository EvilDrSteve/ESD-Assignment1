from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class OrderStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ORDER_STATUS_UNSPECIFIED: _ClassVar[OrderStatus]
    PENDING: _ClassVar[OrderStatus]
    CONFIRMED: _ClassVar[OrderStatus]
    SHIPPED: _ClassVar[OrderStatus]
    DELIVERED: _ClassVar[OrderStatus]
    CANCELLED: _ClassVar[OrderStatus]
ORDER_STATUS_UNSPECIFIED: OrderStatus
PENDING: OrderStatus
CONFIRMED: OrderStatus
SHIPPED: OrderStatus
DELIVERED: OrderStatus
CANCELLED: OrderStatus

class PlaceOrderRequest(_message.Message):
    __slots__ = ("cart_id", "idempotency_key")
    CART_ID_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    cart_id: str
    idempotency_key: str
    def __init__(self, cart_id: _Optional[str] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class PlaceOrderResponse(_message.Message):
    __slots__ = ("order", "replayed")
    ORDER_FIELD_NUMBER: _ClassVar[int]
    REPLAYED_FIELD_NUMBER: _ClassVar[int]
    order: Order
    replayed: bool
    def __init__(self, order: _Optional[_Union[Order, _Mapping]] = ..., replayed: bool = ...) -> None: ...

class OrderItem(_message.Message):
    __slots__ = ("product_id", "name", "price", "quantity", "subtotal")
    PRODUCT_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    PRICE_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    SUBTOTAL_FIELD_NUMBER: _ClassVar[int]
    product_id: str
    name: str
    price: float
    quantity: int
    subtotal: float
    def __init__(self, product_id: _Optional[str] = ..., name: _Optional[str] = ..., price: _Optional[float] = ..., quantity: _Optional[int] = ..., subtotal: _Optional[float] = ...) -> None: ...

class Order(_message.Message):
    __slots__ = ("id", "cart_id", "items", "total", "status", "created_at", "updated_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    CART_ID_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    cart_id: str
    items: _containers.RepeatedCompositeFieldContainer[OrderItem]
    total: float
    status: OrderStatus
    created_at: _timestamp_pb2.Timestamp
    updated_at: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[str] = ..., cart_id: _Optional[str] = ..., items: _Optional[_Iterable[_Union[OrderItem, _Mapping]]] = ..., total: _Optional[float] = ..., status: _Optional[_Union[OrderStatus, str]] = ..., created_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ..., updated_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...
