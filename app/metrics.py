from prometheus_client import Counter, Gauge, Histogram, Summary



# Counters
orders_placed_total = Counter(
    "orders_placed_total",
    "Total number of orders placed"
)

orders_cancelled_total = Counter(
    "orders_cancelled_total",
    "Total number of orders cancelled"
)

http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"]
)

# Gauges
orders_pending = Gauge(
    "orders_pending",
    "Number of orders currently pending"
)

active_carts = Gauge(
    "active_carts",
    "Number of carts with items"
)

# Histogram
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5]
)

# Summary
order_value_dollars = Summary(
    "order_value_dollars",
    "Order value in dollars"
)