"""Build grafana/dashboards/store-a3.json (A3 per-replica dashboard).

  python scripts/build_a3_dashboard.py      # writes the JSON, prints every query

Grafana provisions the file on start (Store folder). Edit here, not in the JSON.
"""
import json
import os
import sys

DS = {"type": "prometheus", "uid": "prometheus"}
COLORS = {"A": "blue", "B": "orange", "C": "green"}

HTTP_SEL = 'job="store",endpoint!~"/metrics|/health"'
GRPC_SEL = 'job="store",grpc_service!="grpc.health.v1.Health"'
HTTP_RATE = f"rate(http_requests_total{{{HTTP_SEL}}}[$window])"
GRPC_RATE = f"rate(grpc_server_handled_total{{{GRPC_SEL}}}[$window])"

panels = []
next_id = [1]
y = [0]


def pid():
    next_id[0] += 1
    return next_id[0]


def replica_overrides():
    return [{
        "matcher": {"id": "byRegexp", "options": f"^{r}( .*)?$"},
        "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}],
    } for r, c in COLORS.items()]


def row(title):
    panels.append({"id": pid(), "type": "row", "title": title, "collapsed": False,
                   "gridPos": {"h": 1, "w": 24, "x": 0, "y": y[0]}, "panels": []})
    y[0] += 1


def ts(title, desc, targets, unit, x, w=12, h=8, extra_defaults=None, stack=False):
    defaults = {"unit": unit, "custom": {"lineWidth": 2, "fillOpacity": 10,
                                         "stacking": {"mode": "normal" if stack else "none"}}}
    defaults.update(extra_defaults or {})
    panels.append({
        "id": pid(), "type": "timeseries", "title": title, "description": desc, "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y[0]},
        "fieldConfig": {"defaults": defaults, "overrides": replica_overrides()},
        "options": {"legend": {"displayMode": "table", "placement": "bottom", "calcs": ["mean", "lastNotNull", "max"]},
                    "tooltip": {"mode": "multi", "sort": "none"}},
        "targets": [{"datasource": DS, "expr": e, "legendFormat": l, "refId": chr(65 + i)}
                    for i, (e, l) in enumerate(targets)],
    })


def bargauge(title, desc, expr, x, w=6, h=6):
    panels.append({
        "id": pid(), "type": "bargauge", "title": title, "description": desc, "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y[0]},
        "fieldConfig": {"defaults": {"unit": "percent", "min": 0, "max": 100, "decimals": 1,
                                     "color": {"mode": "fixed", "fixedColor": "text"}},
                        "overrides": replica_overrides()},
        "options": {"orientation": "horizontal", "displayMode": "basic", "showUnfilled": True,
                    "reduceOptions": {"calcs": ["lastNotNull"], "values": False}},
        "targets": [{"datasource": DS, "expr": expr, "legendFormat": "{{replica}}", "refId": "A", "instant": True}],
    })


def share_expr(counter_sel, window):
    return (f"100 * sum by (replica) (increase({counter_sel}[{window}])) "
            f"/ scalar(sum(increase({counter_sel}[{window}])))")


# ── Summary: share of calls over the whole selected time range ──
row("Share of calls over the selected time range (compare with the load client)")
bargauge("HTTP share per replica [$__range]",
         "Share of HTTP requests (excluding /metrics and /health) handled by each replica over the dashboard's time range. "
         "Set the time range to one experiment run to get its A/B/C split.",
         share_expr(f"http_requests_total{{{HTTP_SEL}}}", "$__range"), 0)
bargauge("gRPC share per replica [$__range]",
         "Share of gRPC calls (excluding health checks) handled by each replica over the dashboard's time range.",
         share_expr(f"grpc_server_handled_total{{{GRPC_SEL}}}", "$__range"), 6)
panels.append({
    "id": pid(), "type": "stat", "title": "Replicas up (Prometheus scrape)", "datasource": DS,
    "description": "1 = Prometheus can scrape the replica directly. Goes to 0 when a replica is stopped.",
    "gridPos": {"h": 6, "w": 6, "x": 12, "y": y[0]},
    "fieldConfig": {"defaults": {"mappings": [{"type": "value", "options": {"0": {"text": "DOWN", "color": "red"},
                                                                        "1": {"text": "UP", "color": "green"}}}],
                                 "color": {"mode": "thresholds"},
                                 "thresholds": {"mode": "absolute", "steps": [{"color": "red", "value": None},
                                                                               {"color": "green", "value": 1}]}},
                    "overrides": []},
    "options": {"colorMode": "background", "graphMode": "none", "textMode": "value_and_name",
                "reduceOptions": {"calcs": ["lastNotNull"], "values": False}},
    "targets": [{"datasource": DS, "expr": 'up{job="store"}', "legendFormat": "{{replica}}", "refId": "A", "instant": True}],
})
panels.append({
    "id": pid(), "type": "stat", "title": "Injected delay per replica (fault switch)", "datasource": DS,
    "description": "Delay added before every request on the replica by `scripts/fault.sh <replica> delay` (0 = off).",
    "gridPos": {"h": 6, "w": 6, "x": 18, "y": y[0]},
    "fieldConfig": {"defaults": {"unit": "s", "color": {"mode": "thresholds"},
                                 "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None},
                                                                               {"color": "orange", "value": 0.001}]}},
                    "overrides": []},
    "options": {"colorMode": "background", "graphMode": "none", "textMode": "value_and_name",
                "reduceOptions": {"calcs": ["lastNotNull"], "values": False}},
    "targets": [{"datasource": DS, "expr": 'injected_delay_seconds{job="store"}', "legendFormat": "{{replica}}",
                 "refId": "A", "instant": True}],
})
y[0] += 6

# ── HTTP ──
row("HTTP: GET /products/{id} and other API calls, per replica (measured on each replica)")
ts("HTTP request rate per replica [$window]",
   "Requests/s handled by each replica, from its own http_requests_total (scraped directly, not through Envoy).",
   [(f"sum by (replica) ({HTTP_RATE})", "{{replica}}"),
    (f"sum({HTTP_RATE})", "total")], "reqps", 0)
ts("HTTP share of total per replica [$window] (%)",
   "Each replica's request rate divided by the total rate over the same window.",
   [(f"100 * sum by (replica) ({HTTP_RATE}) / scalar(sum({HTTP_RATE}))", "{{replica}}")], "percent", 12,
   extra_defaults={"min": 0, "max": 100})
y[0] += 8
ts("HTTP active requests per replica (in-flight gauge, sampled every 2 s)",
   "http_requests_in_flight: requests being handled at the moment of the scrape (includes the injected delay).",
   [(f"sum by (replica) (http_requests_in_flight{{{HTTP_SEL}}})", "{{replica}}")], "short", 0)
ts("HTTP p50 / p95 response time per replica [$window]",
   "Server-side duration from the replica's histogram (middleware start to response), per replica.",
   [(f"histogram_quantile(0.50, sum by (le, replica) (rate(http_request_duration_seconds_bucket{{{HTTP_SEL}}}[$window])))", "{{replica}} p50"),
    (f"histogram_quantile(0.95, sum by (le, replica) (rate(http_request_duration_seconds_bucket{{{HTTP_SEL}}}[$window])))", "{{replica}} p95")],
   "s", 12)
y[0] += 8
ts("HTTP errors per replica by status [$window]",
   "4xx/5xx responses per second, per replica and status code.",
   [(f'sum by (replica, status) (rate(http_requests_total{{{HTTP_SEL},status=~"4..|5.."}}[$window]))', "{{replica}} {{status}}")],
   "reqps", 0, w=24)
y[0] += 8

# ── gRPC ──
row("gRPC: OrderService (GetProduct, PlaceOrder), per replica (measured on each replica)")
ts("gRPC call rate per replica [$window]",
   "Calls/s handled by each replica, from grpc_server_handled_total (health checks excluded).",
   [(f"sum by (replica) ({GRPC_RATE})", "{{replica}}"),
    (f"sum({GRPC_RATE})", "total")], "reqps", 0)
ts("gRPC share of total per replica [$window] (%)",
   "Each replica's call rate divided by the total rate over the same window.",
   [(f"100 * sum by (replica) ({GRPC_RATE}) / scalar(sum({GRPC_RATE}))", "{{replica}}")], "percent", 12,
   extra_defaults={"min": 0, "max": 100})
y[0] += 8
ts("gRPC active calls per replica (in-flight gauge, sampled every 2 s)",
   "grpc_server_in_flight: calls being handled at the moment of the scrape (includes the injected delay).",
   [(f"sum by (replica) (grpc_server_in_flight{{{GRPC_SEL}}})", "{{replica}}")], "short", 0)
ts("gRPC p50 / p95 handling time per replica [$window]",
   "Server-side duration from the replica's histogram (interceptor start to return), per replica.",
   [(f"histogram_quantile(0.50, sum by (le, replica) (rate(grpc_server_handling_seconds_bucket{{{GRPC_SEL}}}[$window])))", "{{replica}} p50"),
    (f"histogram_quantile(0.95, sum by (le, replica) (rate(grpc_server_handling_seconds_bucket{{{GRPC_SEL}}}[$window])))", "{{replica}} p95")],
   "s", 12)
y[0] += 8
ts("gRPC errors per replica by status code [$window]",
   "Non-OK gRPC status codes per second, per replica.",
   [(f'sum by (replica, grpc_code) (rate(grpc_server_handled_total{{{GRPC_SEL},grpc_code!="OK"}}[$window]))', "{{replica}} {{grpc_code}}")],
   "reqps", 0, w=24)
y[0] += 8

# ── Envoy ──
row("Envoy: what the load balancer sees (per cluster)")
ENVOY = 'envoy_cluster_name=~"store_http|store_grpc"'
ts("Envoy upstream requests per cluster [$window]",
   "Requests/s Envoy forwarded to each cluster (store_http, store_grpc). Should match the replicas' totals.",
   [(f"sum by (envoy_cluster_name) (rate(envoy_cluster_upstream_rq_total{{{ENVOY}}}[$window]))", "{{envoy_cluster_name}}")],
   "reqps", 0, w=8)
ts("Envoy active upstream requests per cluster",
   "Requests Envoy is waiting on, per cluster (what least request compares, per host).",
   [(f"envoy_cluster_upstream_rq_active{{{ENVOY}}}", "{{envoy_cluster_name}}")], "short", 8, w=8)
ts("Envoy healthy / total hosts per cluster",
   "membership_healthy drops when active health checks mark a replica unhealthy; membership_total is the configured hosts.",
   [(f"envoy_cluster_membership_healthy{{{ENVOY}}}", "{{envoy_cluster_name}} healthy"),
    (f"envoy_cluster_membership_total{{{ENVOY}}}", "{{envoy_cluster_name}} total")], "short", 16, w=8)
y[0] += 8

dashboard = {
    "title": "Store A3 — Load balancing per replica",
    "uid": "store-a3",
    "description": "A3: three replicas behind Envoy. Per-replica request rate, share, active requests, "
                   "p50/p95 and errors for HTTP and gRPC, plus Envoy's cluster view.",
    "tags": ["a3"],
    "timezone": "browser",
    "refresh": "5s",
    "time": {"from": "now-15m", "to": "now"},
    "schemaVersion": 39,
    "version": 1,
    "templating": {"list": [{
        "name": "window", "label": "rate window", "type": "custom", "query": "30s,1m,2m",
        "current": {"text": "1m", "value": "1m", "selected": True},
        "options": [{"text": w, "value": w, "selected": w == "1m"} for w in ("30s", "1m", "2m")],
        "hide": 0,
    }]},
    "annotations": {"list": []},
    "panels": panels,
}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "grafana", "dashboards", "store-a3.json")
with open(out, "w") as f:
    json.dump(dashboard, f, indent=2, ensure_ascii=False)
    f.write("\n")

# Also print every query for checking against Prometheus
for p in panels:
    for t in p.get("targets", []):
        print(t["expr"])
