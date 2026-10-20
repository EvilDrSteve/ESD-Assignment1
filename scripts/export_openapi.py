"""Write the FastAPI-generated OpenAPI document to openapi.yaml (repo root).

Run: python scripts/export_openapi.py
tests/test_openapi.py fails if the committed file drifts from the code.
"""

import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from main import app  # noqa: E402

OUT = os.path.join(ROOT, "openapi.yaml")


def render() -> str:
    return yaml.safe_dump(app.openapi(), sort_keys=False, allow_unicode=True)


if __name__ == "__main__":
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(render())
    print(f"wrote {OUT}")
