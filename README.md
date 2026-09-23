# Online Store API — ESD Assignment 1

A simple online store API built with FastAPI with full observability: Prometheus metrics, Grafana dashboards, and an ELK logging pipeline.

## Prerequisites

- Docker and Docker Compose installed
- Ports 8050, 9090, 9100, 3000, 9200, 5601 available

## Setup and Run

1. Clone the repository and navigate to the project directory:

```bash
cd esd/assignment-1
```

2. Build and start all services:

```bash
docker compose up --build
```

This starts 8 containers: the app, Prometheus, Grafana, Node Exporter, Elasticsearch, an ES init sidecar (creates the index template then exits), Kibana, and Filebeat.

3. Wait about 30 seconds for all services to become healthy, then access:

| Service | URL |
|---------|-----|
| API | http://localhost:8050 |
| API Docs (Swagger) | http://localhost:8050/docs |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (admin / admin) |
| Kibana | http://localhost:5601 |
| Metrics endpoint | http://localhost:8050/metrics |

## Grafana Setup

1. Open http://localhost:3000 and log in with `admin` / `admin`
2. Go to **Connections → Data Sources → Add data source → Prometheus**
3. Set URL to `http://prometheus:9090`, click **Save & Test**
4. Create a dashboard and add panels using PromQL queries like:
   - `rate(http_requests_total[1m])` — request rate
   - `histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))` — p95 latency
   - `active_carts` — current active carts gauge
5. For Node Exporter metrics: import dashboard ID **1860**

## Kibana Setup

1. Open http://localhost:5601
2. Go to **Management → Data Views → Create data view**
3. Name: `store-logs`, Index pattern: `store-logs-*`
4. Go to **Discover** to search and filter logs
5. Example KQL queries:
   - `severity: WARNING` — show warnings
   - `message: order_placed` — find order events
   - `status: 500` — find server errors

## API Endpoints

### Products
| Method | Path | Description |
|--------|------|-------------|
| GET | `/products` | List all products (optional `?category=electronics`) |
| GET | `/products/{id}` | Get a single product |

### Cart
| Method | Path | Description |
|--------|------|-------------|
| GET | `/cart/{cart_id}` | View cart contents |
| POST | `/cart/{cart_id}/items` | Add item to cart |
| DELETE | `/cart/{cart_id}/items/{product_id}` | Remove item from cart |

### Orders
| Method | Path | Description |
|--------|------|-------------|
| POST | `/orders?cart_id={cart_id}` | Place an order from a cart |
| GET | `/orders` | List all orders (optional `?status=pending`) |
| GET | `/orders/{id}` | Get a single order |
| PUT | `/orders/{id}/confirm` | Confirm a pending order |
| PUT | `/orders/{id}/ship` | Ship a confirmed order |
| PUT | `/orders/{id}/deliver` | Mark a shipped order as delivered |
| PUT | `/orders/{id}/cancel` | Cancel an order (if not delivered) |

### Ops
| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/stats` | Order counts, revenue, active carts |
| GET | `/metrics` | Prometheus metrics |
| POST | `/demo/cardinality?mode=good&count=100` | Cardinality explosion demo |

## Quick Test

```bash
# Browse products
curl -s http://localhost:8050/products | python3 -m json.tool

# Add items to a cart
curl -s -X POST http://localhost:8050/cart/my-cart/items \
  -H "Content-Type: application/json" \
  -d '{"product_id":"p1","quantity":2}' | python3 -m json.tool

# View cart
curl -s http://localhost:8050/cart/my-cart | python3 -m json.tool

# Place an order
curl -s -X POST "http://localhost:8050/orders?cart_id=my-cart" | python3 -m json.tool

# Check stats
curl -s http://localhost:8050/stats | python3 -m json.tool
```

## Fault Injection (Part E)

To enable fault injection, set the environment variable in `docker-compose.yml`:

```yaml
environment:
  - FAULT_INJECTION=true
```

Then rebuild:

```bash
docker compose up --build
```

This injects:
- **500ms latency** on every 5th request
- **HTTP 500 errors** on every 7th request

Both faults are visible in Grafana dashboards and Kibana logs.

## Order Workflow

```
PENDING → CONFIRMED → SHIPPED → DELIVERED
   └────────┴──────────┴──────→ CANCELLED
```

## Stop and Clean Up

```bash
docker compose down          # stop all containers
docker compose down -v       # stop and remove all data volumes
```
