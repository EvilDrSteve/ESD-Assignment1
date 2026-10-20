"""Domain errors shared by the HTTP and gRPC front ends.

Each error knows how it maps to an HTTP status and a gRPC status code, so both
transports report the same business failure consistently.
"""


class DomainError(Exception):
    http_status = 400
    grpc_code = "INVALID_ARGUMENT"
    type = "bad-request"
    title = "Bad request"

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class NotFound(DomainError):
    http_status = 404
    grpc_code = "NOT_FOUND"
    type = "not-found"
    title = "Resource not found"


class InvalidInput(DomainError):
    http_status = 422
    grpc_code = "INVALID_ARGUMENT"
    type = "validation-error"
    title = "Invalid input"


class EmptyCart(DomainError):
    http_status = 409
    grpc_code = "FAILED_PRECONDITION"
    type = "cart-empty"
    title = "Cart is empty"


class InsufficientStock(DomainError):
    http_status = 409
    grpc_code = "FAILED_PRECONDITION"
    type = "insufficient-stock"
    title = "Not enough stock"


class InvalidTransition(DomainError):
    http_status = 409
    grpc_code = "FAILED_PRECONDITION"
    type = "invalid-status-transition"
    title = "Order status cannot change"


class IdempotencyKeyReused(DomainError):
    """Same Idempotency-Key sent with a different request body."""

    http_status = 422
    grpc_code = "INVALID_ARGUMENT"
    type = "idempotency-key-reused"
    title = "Idempotency key reused with a different request"


class IdempotencyInProgress(DomainError):
    """A request with this key is still being processed; client should retry later."""

    http_status = 409
    grpc_code = "ABORTED"
    type = "idempotency-in-progress"
    title = "Request with this idempotency key is in progress"
