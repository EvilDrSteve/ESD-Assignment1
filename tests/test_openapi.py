"""The committed openapi.yaml must match the routes the app actually serves."""

import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_openapi_file_matches_app():
    import main
    with open(os.path.join(ROOT, "openapi.yaml"), encoding="utf-8") as f:
        committed = yaml.safe_load(f)
    assert committed == yaml.safe_load(yaml.safe_dump(main.app.openapi())), \
        "openapi.yaml is stale: run python scripts/export_openapi.py"


def test_spec_documents_errors_for_place_order():
    import main
    op = main.app.openapi()["paths"]["/orders"]["post"]
    for code in ("201", "409", "422", "429"):
        assert code in op["responses"]
    assert "Retry-After" in op["responses"]["429"]["headers"]
    assert op["responses"]["409"]["content"]["application/problem+json"]["schema"]["$ref"] == "#/components/schemas/Problem"
