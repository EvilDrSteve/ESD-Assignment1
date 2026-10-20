import contextvars
import logging
import sys
import os
from pythonjsonlogger import jsonlogger

# Set per request by the HTTP middleware / gRPC interceptor, so every log line
# written while handling that request (including in shared business code)
# carries the same request_id.
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
transport_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("transport", default=None)


class RequestContextFilter(logging.Filter):
    def filter(self, record):
        if not getattr(record, "request_id", None):
            record.request_id = request_id_var.get()
        record.transport = transport_var.get()
        return True


def setup_logger():
    # Logger name becomes the "service" field: store-api (HTTP) or store-grpc
    logger = logging.getLogger(os.environ.get("SERVICE_NAME", "store-api"))
    logger.setLevel(logging.INFO)

    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "severity", "name": "service"},
    )

    logger.addFilter(RequestContextFilter())

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    log_dir = os.environ.get("LOG_DIR", "")
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.environ.get("LOG_FILE", "app.log")
        file_handler = logging.FileHandler(os.path.join(log_dir, log_file))
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


logger = setup_logger()
