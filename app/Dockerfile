# One image, one process per replica (A3): serve.py runs the gRPC server
# (:50051) and the FastAPI app (:8050, also /metrics) side by side.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Regenerate the gRPC stubs from the .proto so the image never uses stale code
RUN python -m grpc_tools.protoc -I proto --python_out=gen --pyi_out=gen --grpc_python_out=gen proto/orders.proto

EXPOSE 8050 50051

CMD ["python", "serve.py"]
