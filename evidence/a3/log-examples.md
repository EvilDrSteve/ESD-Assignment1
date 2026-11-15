# A3 log examples: Envoy event + replica event for one request

Generated from Elasticsearch (index `store-logs-*`) after one HTTP and one gRPC
call through Envoy. Kibana search (KQL) for each: `request_id.keyword : "<id>"`.
Both documents share `request_id`; Envoy's names the upstream (`upstream_host`,
`replica`), the replica's own line says which replica handled it (`replica`).

## HTTP: GET /products/p2 (`request_id` = `trace-http-t1`)

`log_source: envoy`

```json
{
  "@timestamp": "2026-11-15T12:55:02.989Z",
  "client_address": "172.18.0.1",
  "envoy_duration_ms": 9,
  "log_source": "envoy",
  "method": "GET",
  "path": "/products/p2",
  "protocol": "HTTP/1.1",
  "replica": "A",
  "request_id": "trace-http-t1",
  "response_code": 200,
  "response_flags": "-",
  "service": "envoy",
  "start_time": "2026-11-15T12:54:52.512Z",
  "upstream_cluster": "store_http",
  "upstream_host": "172.18.0.11:8050",
  "upstream_ms": "6"
}
```

`log_source: app`

```json
{
  "@timestamp": "2026-11-15T12:54:52.986Z",
  "duration_ms": 4.4,
  "endpoint": "/products/{product_id}",
  "log_source": "app",
  "message": "request",
  "method": "GET",
  "replica": "A",
  "request_id": "trace-http-t1",
  "service": "store",
  "severity": "INFO",
  "status": 200,
  "timestamp": "2026-11-15 12:54:52,520",
  "transport": "http"
}
```

## gRPC: OrderService/GetProduct (`request_id` = `trace-grpc-t1`)

`log_source: envoy`

```json
{
  "@timestamp": "2026-11-15T12:55:02.990Z",
  "client_address": "172.18.0.1",
  "envoy_duration_ms": 14,
  "grpc_status": "OK",
  "log_source": "envoy",
  "method": "POST",
  "path": "/store.v1.OrderService/GetProduct",
  "protocol": "HTTP/2",
  "replica": "A",
  "request_id": "trace-grpc-t1",
  "response_code": 200,
  "response_flags": "-",
  "service": "envoy",
  "start_time": "2026-11-15T12:54:52.737Z",
  "upstream_cluster": "store_grpc",
  "upstream_host": "172.18.0.11:50051",
  "upstream_ms": "10"
}
```

`log_source: app`

```json
{
  "@timestamp": "2026-11-15T12:54:52.986Z",
  "deadline_left_ms": 2981,
  "duration_ms": 1.09,
  "grpc_code": "OK",
  "log_source": "app",
  "message": "rpc",
  "replica": "A",
  "request_id": "trace-grpc-t1",
  "rpc_method": "/store.v1.OrderService/GetProduct",
  "service": "store",
  "severity": "INFO",
  "timestamp": "2026-11-15 12:54:52,749",
  "transport": "grpc"
}
```
