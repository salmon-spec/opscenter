"""Verify public gateway and module auth from the production backend pod."""

import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen


BASE = "http://10.66.66.13:30088"
PATHS = (
    "/modules/k3s/api/v1/summary",
    "/modules/host-inventory/api/v1/hosts",
    "/modules/host-monitor/api/v1/summary",
    "/modules/service-catalog/api/v1/services",
    "/modules/alerts/api/v1/summary",
    "/modules/logs/api/v1/status",
    "/modules/oss-browser/status",
    "/modules/command-code-usage/status",
)


def request(path, token=None, body=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        with urlopen(req, timeout=15) as response:
            return response.status, response.read()
    except HTTPError as exc:
        return exc.code, exc.read()


def main():
    for path in ("/v3/", "/api/v2/health"):
        status, _ = request(path)
        assert status == 200, (path, status)
        print(path, status)
    status, data = request("/api/v2/auth/login", body={
        "username": os.environ["OPS_ADMIN_USER"], "password": os.environ["OPS_ADMIN_PASSWORD"]})
    assert status == 200, ("login", status)
    token = json.loads(data)["access_token"]
    print("login", status)
    for path in PATHS:
        anonymous, _ = request(path)
        signed, payload = request(path, token)
        assert anonymous == 401 and signed == 200, (path, anonymous, signed)
        print(path, "anonymous=401", "signed=200", f"bytes={len(payload)}")


if __name__ == "__main__":
    main()
