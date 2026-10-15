import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.main import app


def test_legacy_certificate_routes_are_removed():
    paths = app.openapi()["paths"]
    assert not any(path.startswith("/api/v2/cert-checks") for path in paths)
