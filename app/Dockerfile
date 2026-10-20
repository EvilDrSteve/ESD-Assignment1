# One image for both front ends; docker-compose picks the command.
#   HTTP: uvicorn main:app          gRPC: python grpc_server.py
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Regenerate the gRPC stubs from the .proto so the image never uses stale code
RUN python -m grpc_tools.protoc -I proto --python_out=gen --pyi_out=gen --grpc_python_out=gen proto/orders.proto

EXPOSE 8050 50051 9101

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8050"]
