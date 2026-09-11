"""ZooKeeper 只读视图（v5.0.0）。

用途：
- 数据服务页面展示 znode 树/节点数据（只读，禁止写操作）；
- Kafka 的元数据依赖（仅展示，不参与业务逻辑）。

环境变量：ZOOKEEPER_HOSTS（host1:2181,host2:2181，空=未配置）。

实现：kazoo 2.10 KazooClient，惰性连接（timeout=MW_PROBE_TIMEOUT，read_only=True），
模块级单例。tree() 广度优先、max_nodes 上限（默认 200），节点按名排序。
全部 fail-open：不可用返回空树/None，不抛异常。
"""

from __future__ import annotations

import base64
import logging
import threading
import time

from app.config import MW_PROBE_TIMEOUT, ZOOKEEPER_HOSTS

logger = logging.getLogger("opscenter.zk")

_STATUS_CACHE_TTL = 10.0


class ZKView:
    def __init__(self) -> None:
        self._client = None
        self._client_lock = threading.Lock()
        self._start_failed = False
        self._status_lock = threading.Lock()
        self._status_at = 0.0
        self._status_value: dict | None = None

    def _configured(self) -> bool:
        return bool(ZOOKEEPER_HOSTS)

    def _backend(self):
        if not self._configured() or self._start_failed:
            return None
        with self._client_lock:
            if self._client is None:
                try:
                    from kazoo.client import KazooClient
                    client = KazooClient(
                        hosts=ZOOKEEPER_HOSTS,
                        timeout=max(1.0, MW_PROBE_TIMEOUT),
                        read_only=True,
                    )
                    client.start(timeout=max(1.0, MW_PROBE_TIMEOUT))
                    self._client = client
                except Exception as exc:
                    logger.info("zookeeper connect failed: %s", exc)
                    self._start_failed = True
                    self._client = None
            return self._client

    def available(self) -> bool:
        return bool(self.status().get("available"))

    def status(self) -> dict:
        now = time.monotonic()
        with self._status_lock:
            if self._status_value and now - self._status_at < _STATUS_CACHE_TTL:
                return dict(self._status_value)
        result = {
            "kind": "zookeeper",
            "configured": self._configured(),
            "available": False,
            "latency_ms": None,
            "error": "",
            "detail": {"endpoints": ZOOKEEPER_HOSTS, "state": ""},
        }
        client = self._backend()
        if client is None:
            if self._configured() and self._start_failed:
                result["error"] = "connect failed"
            with self._status_lock:
                self._status_at = now
                self._status_value = dict(result)
            return dict(result)
        started = time.perf_counter()
        try:
            state = str(getattr(client, "state", "") or "")
            result["detail"]["state"] = state
            if client.exists("/"):
                result["available"] = True
        except Exception as exc:
            result["error"] = str(exc)[:300]
        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        with self._status_lock:
            self._status_at = now
            self._status_value = dict(result)
        return dict(result)

    def tree(self, path: str = "/", depth: int = 2, max_nodes: int = 200) -> dict:
        if not path.startswith("/"):
            path = "/" + path
        root = {"path": path, "children": [], "truncated": False}
        client = self._backend()
        if client is None:
            return root
        max_nodes = max(1, min(int(max_nodes), 1000))
        depth = max(0, min(int(depth), 6))
        frontier = [(path, 0)]
        created = 0
        while frontier:
            current, level = frontier.pop(0)
            if level >= depth:
                continue
            try:
                names = sorted(client.get_children(current) or [])
            except Exception:
                continue
            for name in names:
                if name in (".", ".."):
                    continue
                child_path = current.rstrip("/") + "/" + name
                node = {"name": name, "path": child_path, "has_children": False}
                if created >= max_nodes:
                    root["truncated"] = True
                    return root
                created += 1
                if level + 1 < depth:
                    try:
                        node["has_children"] = bool(client.get_children(child_path))
                    except Exception:
                        node["has_children"] = False
                root["children"].append(node)
                if level + 1 < depth:
                    frontier.append((child_path, level + 1))
        return root

    def get(self, path: str) -> dict | None:
        if not path.startswith("/"):
            path = "/" + path
        client = self._backend()
        if client is None:
            return None
        try:
            if not client.exists(path):
                return None
            data, stat = client.get(path)
            try:
                text = data.decode("utf-8") if data else ""
            except Exception:
                text = "base64:" + base64.b64encode(data or b"").decode("ascii")
            return {
                "path": path,
                "data": text,
                "size": stat.dataLength,
                "mtime": stat.mtime,
                "version": stat.version,
                "num_children": stat.numChildren,
            }
        except Exception:
            return None

    def close(self) -> None:
        with self._client_lock:
            client, self._client = self._client, None
        if client is not None:
            try:
                client.stop()
                client.close()
            except Exception:
                pass


zk_view = ZKView()
