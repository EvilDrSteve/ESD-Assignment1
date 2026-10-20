#!/bin/bash
# Regenerate the Python gRPC code from app/proto/orders.proto into app/gen/.
# (The Docker build runs the same command.)
set -e
cd "$(dirname "$0")/../app"
python -m grpc_tools.protoc -I proto --python_out=gen --pyi_out=gen --grpc_python_out=gen proto/orders.proto
echo "generated: $(ls gen)"
