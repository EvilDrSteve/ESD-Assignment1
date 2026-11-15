"""Container entrypoint (A3): one process serves both front ends of a replica.

  gRPC  :50051  store.v1.OrderService (started first, runs on its own thread pool)
  HTTP  :8050   FastAPI app, plus /metrics for both HTTP and gRPC (one registry)

uvicorn owns the main thread and handles SIGTERM. When it returns, the gRPC
server is drained too, so `docker compose stop` shuts the replica down cleanly.
"""

import os

import uvicorn

from grpc_server import GRPC_PORT, build_server
from logger import logger

HTTP_PORT = int(os.environ.get("HTTP_PORT", "8050"))


def run():
    grpc_server = build_server(GRPC_PORT)
    grpc_server.start()
    logger.info("grpc_started", extra={"grpc_port": GRPC_PORT})
    try:
        # Keep idle connections open longer than Envoy does (30 s), so Envoy is
        # always the side that closes them and never reuses one being closed
        uvicorn.run("main:app", host="0.0.0.0", port=HTTP_PORT, timeout_keep_alive=75)
    finally:
        logger.info("grpc_stopping")
        grpc_server.stop(grace=5).wait()


if __name__ == "__main__":
    run()
