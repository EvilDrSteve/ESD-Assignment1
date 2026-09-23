"""
Cardinality explosion demo for Part E.2.

Run with: python cardinality_demo.py [--with-label]

--with-label: adds a unique request_id label to each counter increment (bad practice)
Without flag: increments the counter without the request_id label (correct practice)

The /metrics endpoint on port 8051 lets Prometheus scrape the results.
After 100 requests, the server keeps running so Prometheus can scrape.
"""

from prometheus_client import Counter, start_http_server, generate_latest
import time
import uuid
import sys

WITH_LABEL = "--with-label" in sys.argv

if WITH_LABEL:
    demo_requests = Counter(
        "demo_requests_total",
        "Demo counter with high-cardinality label",
        ["request_id"],
    )
    print("Mode: WITH request_id label (cardinality explosion)")
else:
    demo_requests = Counter(
        "demo_requests_total",
        "Demo counter without high-cardinality label",
    )
    print("Mode: WITHOUT request_id label (correct)")

start_http_server(8051)
print("Metrics at http://localhost:8051/metrics")
print("Sending 100 requests...")

for i in range(1, 101):
    if WITH_LABEL:
        demo_requests.labels(request_id=str(uuid.uuid4())).inc()
    else:
        demo_requests.inc()

    if i % 10 == 0:
        print(f"  {i}/100 requests sent")
    time.sleep(0.1)

print("Done. Keeping server running for Prometheus scrapes. Ctrl+C to stop.")

while True:
    time.sleep(1)
