"""Create and archive disposable host/service rows through the public test gateway."""

import json
import os
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen


BASE = "http://web"
MARKER = "Disposable test record; archived by verify_module_write_smoke.py"


def request(method, path, token, body=None):
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    data = None if body is None else json.dumps(body).encode()
    if data is not None:
        headers["Content-Type"] = "application/json"
    try:
        with urlopen(Request(BASE + path, data=data, headers=headers, method=method), timeout=20) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except HTTPError as error:
        return error.code, None


status, login = request(
    "POST",
    "/api/v2/auth/login",
    "",
    {"username": os.environ.get("OPS_ADMIN_USER", "admin"), "password": os.environ["OPS_ADMIN_PASSWORD"]},
)
assert status == 200, f"login: {status}"
token = login["access_token"]
suffix = uuid.uuid4().hex[:10]
host_name = f"auth-smoke-{suffix}"
host_id = service_id = None

status, services = request("GET", "/modules/service-catalog/api/v1/services", token)
assert status == 200, f"service list before smoke: {status}"
for item in services:
    if item.get("name", "").startswith("auth-smoke-") and item.get("description") == MARKER:
        status, _ = request("POST", f"/modules/service-catalog/api/v1/services/manual/{item['service_id']}/archive", token)
        assert status == 200, f"previous service archive: {status}"
status, hosts = request("GET", "/modules/host-inventory/api/v1/hosts", token)
assert status == 200, f"host list before smoke: {status}"
for item in hosts["items"]:
    if item.get("name", "").startswith("auth-smoke-") and item.get("remark") == MARKER:
        status, _ = request("POST", f"/modules/host-inventory/api/v1/hosts/{item['id']}/archive", token)
        assert status == 200, f"previous host archive: {status}"

try:
    status, host = request("POST", "/modules/host-inventory/api/v1/hosts", token, {
        "name": host_name,
        "host": f"{host_name}.invalid",
        "tags": ["temporary-auth-smoke"],
        "remark": MARKER,
    })
    assert status == 201, f"host create: {status}"
    host_id = host["id"]
    status, hosts = request("GET", "/modules/host-inventory/api/v1/hosts", token)
    assert status == 200 and any(item["id"] == host_id for item in hosts["items"]), "host read-after-write failed"

    status, service = request("POST", "/modules/service-catalog/api/v1/services", token, {
        "server_id": host_id,
        "server_name": host_name,
        "server_host": f"{host_name}.invalid",
        "name": host_name,
        "url": "http://127.0.0.1:1/opscenter-auth-smoke",
        "category": "test",
        "description": MARKER,
    })
    assert status == 201, f"service create: {status}"
    service_id = service["id"]
    status, services = request("GET", "/modules/service-catalog/api/v1/services", token)
    assert status == 200 and any(item.get("service_id") == service_id for item in services), "service read-after-write failed"
    print("HOST_CREATE_READ=PASS SERVICE_CREATE_READ=PASS")
finally:
    cleanup_errors = []
    if service_id:
        status, _ = request("POST", f"/modules/service-catalog/api/v1/services/manual/{service_id}/archive", token)
        if status != 200:
            cleanup_errors.append(f"service archive: {status}")
    if host_id:
        status, _ = request("POST", f"/modules/host-inventory/api/v1/hosts/{host_id}/archive", token)
        if status != 200:
            cleanup_errors.append(f"host archive: {status}")
    assert not cleanup_errors, "; ".join(cleanup_errors)

status, hosts = request("GET", "/modules/host-inventory/api/v1/hosts", token)
assert status == 200 and all(item["id"] != host_id for item in hosts["items"]), "host archive verification failed"
status, services = request("GET", "/modules/service-catalog/api/v1/services", token)
assert status == 200 and all(item.get("service_id") != service_id for item in services), "service archive verification failed"
print("HOST_ARCHIVE=PASS SERVICE_ARCHIVE=PASS")
