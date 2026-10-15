"""Read-only end-to-end checks for the unified-login test gateway."""

import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen


BASE = "http://web"


def status(path, token=None, *, method="GET", body=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if body is None else json.dumps(body).encode()
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, b""


login_status, login_body = status(
    "/api/v2/auth/login",
    method="POST",
    body={"username": os.environ.get("OPS_ADMIN_USER", "admin"), "password": os.environ["OPS_ADMIN_PASSWORD"]},
)
assert login_status == 200, f"login: {login_status}"
token = json.loads(login_body)["access_token"]

checks = (
    ("/api/v2/auth/me", 200),
    ("/api/v2/screen/summary", 200),
    ("/api/v2/services/health", 200),
    ("/api/v2/keys", 200),
    ("/api/v2/modules", 200),
    ("/modules/host-inventory/api/v1/auth/me", 200),
    ("/modules/service-catalog/api/v1/auth/me", 200),
    ("/modules/host-monitor/api/v1/summary", 200),
    ("/modules/alerts/api/v1/events", 200),
    ("/modules/logs/api/v1/status", 200),
    ("/modules/k3s/api/v1/summary", 200),
    ("/api/v2/auth/me", 200),
)
for path, expected in checks:
    actual, _ = status(path, token)
    print(f"{actual} {path}")
    assert actual == expected, f"{path}: expected {expected}, got {actual}"

for path in ("/api/v2/auth/me", "/api/v2/screen/summary", "/api/v2/services/health"):
    actual, _ = status(path)
    print(f"anonymous {actual} {path}")
    assert actual == 401, f"{path}: anonymous access not denied"

for path in (
    "/modules/host-inventory/api/v1/hosts",
    "/modules/service-catalog/api/v1/services",
    "/modules/host-monitor/api/v1/summary",
    "/modules/alerts/api/v1/events",
    "/modules/logs/api/v1/status",
    "/modules/k3s/api/v1/summary",
):
    actual, _ = status(path)
    print(f"anonymous {actual} {path}")
    assert actual == 401, f"{path}: anonymous access not denied"
