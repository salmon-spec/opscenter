"""Non-mutating gateway checks for the VM100 unified-login test stack."""

import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.auth import ADMIN_USER, create_access_token


base = "http://web"
token = create_access_token(1, ADMIN_USER)
checks = (
    ("GET", "/modules/host-inventory/api/v1/auth/me", None, 200),
    ("GET", "/modules/service-catalog/api/v1/auth/me", None, 200),
    ("GET", "/api/v2/modules", None, 200),
    ("GET", "/api/v2/services/plaza/gitea/detail", None, 200),
    ("GET", "/api/v2/services/plaza/gitea/credentials", None, 200),
    ("POST", "/modules/host-inventory/api/v1/hosts", {}, 422),
    ("POST", "/modules/service-catalog/api/v1/services", {}, 422),
    ("POST", "/api/v2/servers", {}, 422),
    ("POST", "/api/v2/alert-rules", {}, 422),
)

for method, path, body, expected in checks:
    data = None if body is None else json.dumps(body).encode()
    request = Request(
        base + path,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=12) as response:
            status = response.status
    except HTTPError as error:
        status = error.code
    print(f"{status} {method} {path}")
    assert status == expected, f"expected {expected} for {path}, got {status}"

for path in (
    "/modules/host-inventory/api/v1/hosts",
    "/modules/service-catalog/api/v1/services",
    "/modules/host-monitor/api/v1/summary",
    "/modules/alerts/api/v1/events",
    "/modules/logs/api/v1/status",
    "/modules/k3s/api/v1/summary",
):
    request = Request(base + path)
    try:
        with urlopen(request, timeout=12) as response:
            status = response.status
    except HTTPError as error:
        status = error.code
    print(f"{status} GET {path} (anonymous)")
    assert status == 401, f"anonymous request to {path} was not denied"
