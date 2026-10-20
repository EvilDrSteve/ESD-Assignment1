from prometheus_client import Counter, Gauge, Histogram

# Latency buckets shared by HTTP and gRPC so their quantiles are comparable.
# Finer resolution below 100 ms, where nearly all store calls land.
LATENCY_BUCKETS = [0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]


# ── Business metrics (incremented by orders_service, so both transports report them) ──

orders_placed_total = Counter(
    "orders_placed_total",
    "Total number of orders placed",
    ["transport"],  # http | grpc
)

orders_cancelled_total = Counter(
    "orders_cancelled_total",
    "Total number of orders cancelled"
)

checkout_failures_total = Counter(
    "checkout_failures_total",
    "Place-order attempts rejected by a business rule",
    ["transport", "reason"],  # reason = DomainError.type, a fixed set
)

idempotent_replays_total = Counter(
    "idempotent_replays_total",
    "Place-order requests answered from a stored idempotency record instead of creating a new order",
    ["transport"],
)

# Histogram instead of A1's Summary: it can be aggregated across the two processes
order_value_dollars = Histogram(
    "order_value_dollars",
    "Order value in dollars",
    buckets=[10, 25, 50, 100, 250, 500, 1000],
)

# ── HTTP metrics (only the FastAPI process records these) ──

http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"]
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds, measured server-side in middleware",
    ["method", "endpoint"],
    buckets=LATENCY_BUCKETS,
)

http_requests_in_flight = Gauge(
    "http_requests_in_flight",
    "HTTP requests currently being handled",
    ["method", "endpoint"],
)

http_rate_limited_total = Counter(
    "http_rate_limited_total",
    "Requests rejected with 429 by the rate limiter",
    ["endpoint"],
)

# orders_pending and active_carts live in main.py: they are read from Redis at
# scrape time and must be exported by one process only.
