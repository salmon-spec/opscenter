"""Nacos 配置中心 / 注册中心客户端（v5.0.0）。

用途：
- 动态配置：`get_overlay()` 读取 `NACOS_CONFIG_DATA_ID`（JSON）供运行时覆盖；
  `publish_config()` 启动时发布本实例信息（非敏感）；
- 服务注册：`register_instance()` 可选注册 OpsCenter 实例；
- 只读浏览：configs / services 列表（数据服务页面）。

环境变量：NACOS_URL、NACOS_GROUP、NACOS_CONFIG_DATA_ID、NACOS_USERNAME/PASSWORD（可选）、
NACOS_ENABLED（publish/register 需要 true；读取浏览不受开关限制）。

实现：httpx 直连 OpenAPI（不引第三方 SDK）。鉴权开启时走 v1 accessToken 流程
（POST /nacos/v1/auth/login，缓存到过期前 60s），失败降级为直接 username/password。
全部 fail-open，超时 MW_PROBE_TIMEOUT。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

import httpx

from app.config import (
    MW_PROBE_TIMEOUT,
    NACOS_CONFIG_DATA_ID,
    NACOS_ENABLED,
    NACOS_GROUP,
    NACOS_PASSWORD,
    NACOS_URL,
    NACOS_USERNAME,
)

logger = logging.getLogger("opscenter.nacos")

_STATUS_CACHE_TTL = 10.0
_MAX_INSTANCE_SERVICES = 20


def _endpoint() -> str:
    """host:port（无密码），供 status().detail.endpoint。"""
    if not NACOS_URL:
        return ""
    try:
        from urllib.parse import urlsplit
        parts = urlsplit(NACOS_URL if "://" in NACOS_URL else f"http://{NACOS_URL}")
        port = parts.port or 8848
        return f"{parts.hostname}:{port}"
    except Exception:
        return ""


class NacosConfig:
    def __init__(self) -> None:
        self._client: httpx.Client | None = None
        self._client_lock = threading.Lock()
        self._token = ""
        self._token_exp = 0.0
        self._status_lock = threading.Lock()
        self._status_at = 0.0
        self._status_value: dict | None = None

    # ── 内部 ──
    def _configured(self) -> bool:
        return bool(NACOS_URL)

    def _backend(self) -> httpx.Client | None:
        if not self._configured():
            return None
        with self._client_lock:
            if self._client is None:
                self._client = httpx.Client(base_url=NACOS_URL, timeout=MW_PROBE_TIMEOUT)
            return self._client

    def _auth_params(self) -> dict:
        if not NACOS_USERNAME:
            return {}
        if self._token and time.time() < self._token_exp - 60:
            return {"accessToken": self._token}
        try:
            client = self._backend()
            if client is None:
                return {}
            resp = client.post(
                "/nacos/v1/auth/login",
                data={"username": NACOS_USERNAME, "password": NACOS_PASSWORD},
            )
            if resp.status_code == 200:
                body = resp.json() or {}
                token = body.get("accessToken")
                if token:
                    self._token = str(token)
                    self._token_exp = time.time() + float(body.get("tokenTtl") or 18000)
                    return {"accessToken": self._token}
        except Exception:
            pass
        return {"username": NACOS_USERNAME, "password": NACOS_PASSWORD}

    def _get(self, path: str, params: dict | None = None) -> httpx.Response | None:
        client = self._backend()
        if client is None:
            return None
        merged = dict(params or {})
        merged.update(self._auth_params())
        return client.get(path, params=merged)

    def _post_form(self, path: str, data: dict) -> bool:
        client = self._backend()
        if client is None:
            return False
        form = dict(data)
        form.update(self._auth_params())
        resp = client.post(path, data=form)
        return resp.status_code == 200 and (resp.text or "").strip().lower() in ("true", "ok", "")

    # ── 公开接口 ──
    def available(self) -> bool:
        return bool(self.status().get("available"))

    def status(self) -> dict:
        now = time.monotonic()
        with self._status_lock:
            if self._status_value and now - self._status_at < _STATUS_CACHE_TTL:
                return dict(self._status_value)
        result = {
            "kind": "nacos",
            "configured": self._configured(),
            "available": False,
            "latency_ms": None,
            "error": "",
            "detail": {"endpoint": _endpoint(), "group": NACOS_GROUP, "data_id": NACOS_CONFIG_DATA_ID},
        }
        if not result["configured"]:
            with self._status_lock:
                self._status_at = now
                self._status_value = dict(result)
            return dict(result)
        started = time.perf_counter()
        try:
            resp = self._get("/nacos/v1/console/health/readiness")
            if resp is not None and resp.status_code == 200:
                result["available"] = True
        except Exception as exc:
            result["error"] = str(exc)[:300]
        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        with self._status_lock:
            self._status_at = now
            self._status_value = dict(result)
        return dict(result)

    def get_config(self, data_id: str | None = None, group: str | None = None) -> str | None:
        try:
            resp = self._get(
                "/nacos/v1/cs/configs",
                {"dataId": data_id or NACOS_CONFIG_DATA_ID, "group": group or NACOS_GROUP},
            )
            if resp is not None and resp.status_code == 200 and (resp.text or "").strip():
                return resp.text
        except Exception:
            pass
        return None

    def publish_config(self, content: str, data_id: str | None = None,
                       group: str | None = None) -> bool:
        if not NACOS_ENABLED:
            return False
        try:
            return self._post_form("/nacos/v1/cs/configs", {
                "dataId": data_id or NACOS_CONFIG_DATA_ID,
                "group": group or NACOS_GROUP,
                "type": "json",
                "content": content,
            })
        except Exception:
            return False

    def list_configs(self, *, group: str | None = None, page: int = 1, size: int = 50) -> dict:
        try:
            # Nacos v1：精确查询需要 dataId；列全量用 blur + 通配
            params = {
                "search": "blur", "dataId": "*",
                "pageNo": max(1, int(page)), "pageSize": max(1, int(size)),
                "group": group or "*",
            }
            resp = self._get("/nacos/v1/cs/configs", params)
            if resp is None or resp.status_code != 200:
                return {"total": 0, "configs": []}
            body = resp.json() or {}
            items = body.get("pageItems") or []
            configs = [{
                "data_id": it.get("dataId", ""),
                "group": it.get("group", ""),
                "updated_at": it.get("modifyTime"),
            } for it in items]
            return {"total": int(body.get("totalCount") or len(configs)), "configs": configs}
        except Exception:
            return {"total": 0, "configs": []}

    def list_services(self, *, strict: bool = False, errors: list[str] | None = None) -> list[dict]:
        try:
            resp = self._get("/nacos/v1/ns/service/list", {"pageNo": 1, "pageSize": 100})
            if resp is None or resp.status_code != 200:
                if strict:
                    raise RuntimeError(f"service list HTTP {getattr(resp, 'status_code', 'unavailable')}")
                return []
            body = resp.json() or {}
            names = body.get("doms") or []
            services = []
            for name in names:
                services.append({"name": name, "group": NACOS_GROUP, "instance_count": 0, "healthy_count": 0})
            for svc in services[:_MAX_INSTANCE_SERVICES]:
                try:
                    inst = self._get("/nacos/v1/ns/instance/list", {"serviceName": svc["name"]})
                    if inst is None or inst.status_code != 200:
                        if strict:
                            svc["instance_count"] = None
                            svc["healthy_count"] = None
                            if errors is not None:
                                errors.append(f"service {svc['name']}: instance list unavailable")
                        continue
                    hosts = (inst.json() or {}).get("hosts") or []
                    svc["instance_count"] = len(hosts)
                    svc["healthy_count"] = sum(1 for h in hosts if h.get("enabled", True) and h.get("healthy", False))
                except Exception:
                    if strict:
                        svc["instance_count"] = None
                        svc["healthy_count"] = None
                        if errors is not None:
                            errors.append(f"service {svc['name']}: instance query failed")
                    continue
            return services
        except Exception:
            if strict:
                raise
            return []

    def monitor_metrics(self) -> dict:
        errors: list[str] = []
        try:
            services = self.list_services(strict=True, errors=errors)
        except Exception as exc:
            return {"metrics": [], "errors": [f"services: {exc}"]}
        configs = self.list_configs(page=1, size=1)
        rows: list[dict] = []

        def add(key: str, label: str, value: Any, unit: str = "count",
                dimensions: dict | None = None, source: str = "openapi") -> None:
            if value is None:
                return
            rows.append({"key": key, "label": label, "value": value, "unit": unit,
                         "dimensions": dimensions or {}, "source": source})

        known = [item for item in services if item.get("instance_count") is not None
                 and item.get("healthy_count") is not None]
        instance_count = sum(int(item.get("instance_count") or 0) for item in known)
        healthy_count = sum(int(item.get("healthy_count") or 0) for item in known)
        add("nacos_services_total", "服务数", len(services))
        if len(known) == len(services):
            add("nacos_instances_total", "实例数", instance_count)
            add("nacos_healthy_instances", "健康实例数", healthy_count)
            add("nacos_unhealthy_instances", "异常实例数", max(0, instance_count - healthy_count))
        add("nacos_configs_total", "配置数",
            configs.get("total") if isinstance(configs, dict) else None)

        for service in services[:200]:
            name = str(service.get("name") or "")
            if not name:
                continue
            dims = {"service": name, "group": str(service.get("group") or NACOS_GROUP)}
            add("nacos_service_instances", "服务实例数", service.get("instance_count"), "count", dims)
            add("nacos_service_healthy_instances", "服务健康实例数", service.get("healthy_count"), "count", dims)
        return {"metrics": rows, "errors": errors}

    def get_overlay(self) -> dict:
        raw = self.get_config()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def register_instance(self, service_name: str, ip: str, port: int,
                          metadata: dict | None = None) -> bool:
        if not NACOS_ENABLED:
            return False
        try:
            form: dict[str, Any] = {
                "serviceName": service_name, "ip": ip, "port": str(port),
                "groupName": NACOS_GROUP, "healthy": "true", "enabled": "true",
            }
            if metadata:
                merged = dict(metadata)
                form["metadata"] = json.dumps(merged, ensure_ascii=False)
            return self._post_form("/nacos/v1/ns/instance", form)
        except Exception:
            return False


nacos_config = NacosConfig()
