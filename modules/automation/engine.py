"""Fixed-project Semaphore API adapter. No arbitrary paths or browser credentials."""
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException


FIELDS = {
    "templates": ("id", "name", "description", "playbook", "inventory_id", "repository_id", "environment_id", "app"),
    "tasks": ("id", "template_id", "status", "created", "start", "end", "message", "dry_run"),
    "inventories": ("id", "name", "type", "inventory", "ssh_key_id"),
    "schedules": ("id", "name", "template_id", "cron_format", "active", "type", "run_at"),
    "repositories": ("id", "name"), "environments": ("id", "name"), "keys": ("id", "name", "type"),
}


def clean(kind, row):
    result = {key: row[key] for key in FIELDS[kind] if key in row}
    if kind == "tasks" and isinstance(row.get("params"), dict) and isinstance(row["params"].get("dry_run"), bool):
        result["dry_run"] = row["params"]["dry_run"]
    return result


class Semaphore:
    def __init__(self, url, project_id, token_file, client=None):
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("SEMAPHORE_URL must be an HTTP(S) base URL without credentials/query")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1", "semaphore"}:
            raise ValueError("Use HTTPS for a remote Semaphore engine")
        if project_id < 1:
            raise ValueError("SEMAPHORE_PROJECT_ID must be positive")
        self.url, self.project_id, self.token_file = url.rstrip("/"), project_id, token_file
        self.client = client

    def request(self, method, path, body=None):
        try:
            token = Path(self.token_file).read_text(encoding="utf-8").strip()
        except OSError:
            raise HTTPException(503, "Semaphore API 凭据文件不可用") from None
        if not token:
            raise HTTPException(503, "Semaphore API 凭据未配置")
        client = self.client or httpx.Client(timeout=20, follow_redirects=False, trust_env=False)
        try:
            # Stream limits both successful results and error bodies; never retry mutations.
            with client.stream(method, f"{self.url}/api/project/{self.project_id}/{path}",
                               headers={"Authorization": f"Bearer {token}"}, json=body) as response:
                if not 200 <= response.status_code < 300:
                    code = response.status_code if response.status_code in {400, 404, 409, 422} else 502
                    raise HTTPException(code, f"Semaphore 请求失败（HTTP {response.status_code}）")
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 4 * 1024 * 1024:
                        raise HTTPException(502, "响应超过 4 MiB，请在 Semaphore 中缩小查询范围")
                    chunks.append(chunk)
                raw = b"".join(chunks)
                return json.loads(raw) if raw else None
        except httpx.TimeoutException:
            raise HTTPException(504, "执行引擎超时，操作结果可能已生效。请刷新核对，勿重复提交。") from None
        except (httpx.HTTPError, ValueError):
            raise HTTPException(502, "Semaphore 返回无效数据或暂不可用") from None
        finally:
            if self.client is None:
                client.close()

    def list(self, kind):
        route = {"inventories": "inventory", "environments": "environment"}.get(kind, kind)
        suffix = "?sort=name&order=asc" if kind in {"templates", "inventories", "repositories", "environments", "keys"} else ""
        data = self.request("GET", route + suffix)
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise HTTPException(502, "Semaphore 列表格式不兼容")
        return [clean(kind, row) for row in data]

    def save(self, kind, value, item_id=None):
        route = {"inventories": "inventory"}.get(kind, kind)
        payload = {**value, "project_id": self.project_id}
        if kind == "tasks":
            payload["params"] = {"dry_run": payload.pop("dry_run")}
        if item_id is not None:
            payload["id"] = item_id
        response = self.request("PUT" if item_id is not None else "POST",
                                route + (f"/{item_id}" if item_id is not None else ""), payload)
        return clean(kind, response) if isinstance(response, dict) else {"id": item_id}
