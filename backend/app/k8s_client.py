"""Kubernetes 只读 API 客户端（httpx 薄封装，需求基线 2026-09-10 §4.2/§11）。

- 严格只读：本模块所有便捷方法仅使用 GET；禁止 kubectl 子进程。
- 配置优先级：① K8S_API_URL + K8S_TOKEN（env 模式）② in_cluster（Pod 内
  ServiceAccount）③ OPS_KUBECONFIG（kubeconfig 文件；仅当运行环境已装 PyYAML 时支持，
  未安装时 status() 明确提示，不新增依赖）。
- 有界重试：连接错误/5xx 重试 1 次；4xx 不重试。并发受 K8S_MAX_CONCURRENCY 限制。
- 凭证（token/CA）只存内存；错误信息一律脱敏。
- 提供模块级 get_client()/reset_client()/list_nodes() 供 topology.py 与
  server_details.py 的接缝使用（他们做了延迟导入+异常包裹）。
"""

from __future__ import annotations

import base64
import os
import re
import threading
import time
from typing import Any, Optional

import httpx

from app.config import (
    K8S_API_URL,
    K8S_CA_PATH,
    K8S_INSECURE_TLS,
    K8S_KUBECONFIG,
    K8S_MAX_CONCURRENCY,
    K8S_REQUEST_TIMEOUT,
    K8S_TOKEN,
    K8S_TOKEN_FILE,
)

_IN_CLUSTER_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
_IN_CLUSTER_CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"

_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


def valid_k8s_name(value: str) -> bool:
    return bool(value) and bool(_NAME_RE.match(value)) and len(value) <= 253


def sanitize_error(exc: Exception) -> str:
    """错误脱敏：绝不携带 token/Authorization/证书内容。"""
    text = str(exc)
    for marker in ("Bearer ", "Authorization", "-----BEGIN", "token=", "client-secret"):
        text = text.replace(marker, "***")
    return text[:300]


class K8sError(Exception):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class K8sClient:
    """单集群只读客户端。测试可注入 transport（httpx.MockTransport）。"""

    def __init__(
        self,
        transport: Optional[httpx.BaseTransport] = None,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        ca_path: Optional[str] = None,
        insecure: Optional[bool] = None,
        timeout: Optional[float] = None,
    ):
        mode, url, tok, verify = self._resolve_config(base_url, token, ca_path, insecure)
        self.mode = mode
        self.base_url = (url or "").rstrip("/")
        self._token = tok or ""
        self._timeout = timeout or K8S_REQUEST_TIMEOUT
        self._sem = threading.Semaphore(max(1, K8S_MAX_CONCURRENCY))
        self._last_error: str = ""
        if mode == "none":
            self._client = None
            return
        if mode == "in_cluster":
            verify_opt = _IN_CLUSTER_CA_PATH if os.path.exists(_IN_CLUSTER_CA_PATH) else True
        else:
            verify_opt = False if verify is False else (verify if verify else True)
        try:
            self._client = httpx.Client(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
                verify=verify_opt,
                timeout=httpx.Timeout(connect=3.0, read=self._timeout, write=3.0, pool=3.0),
                transport=transport,
                follow_redirects=False,
            )
        except Exception as exc:  # 构造失败 → 不可用
            self._client = None
            self.mode = "none"
            self._last_error = sanitize_error(exc)

    # ── 配置解析 ──────────────────────────────────────────────
    def _resolve_config(self, base_url, token, ca_path, insecure):
        insecure_flag = insecure if insecure is not None else K8S_INSECURE_TLS
        url = base_url if base_url is not None else K8S_API_URL
        tok = token if token is not None else K8S_TOKEN
        # token 文件优先（避免明文进环境变量）
        token_file = K8S_TOKEN_FILE
        if token_file and not tok:
            try:
                with open(token_file, "r", encoding="utf-8") as f:
                    tok = f.read().strip()
            except Exception as exc:
                return "none", "", "", f"token 文件读取失败：{sanitize_error(exc)}"
        ca = ca_path if ca_path is not None else K8S_CA_PATH
        if url and tok:
            return "env", url, tok, (False if insecure_flag else ca)
        if os.path.exists(_IN_CLUSTER_TOKEN_PATH):
            try:
                with open(_IN_CLUSTER_TOKEN_PATH, "r", encoding="utf-8") as f:
                    tok = f.read().strip()
                host = os.environ.get("KUBERNETES_SERVICE_HOST", "")
                port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", os.environ.get("KUBERNETES_SERVICE_PORT", "443"))
                if host:
                    return "in_cluster", f"https://{host}:{port}", tok, _IN_CLUSTER_CA_PATH
            except Exception as exc:
                return "none", "", "", sanitize_error(exc)
        kc = K8S_KUBECONFIG
        if kc:
            try:
                import yaml  # 可选依赖：未安装则明确提示，不新增 requirements
            except ImportError:
                return "none", "", "", "kubeconfig 模式需要 PyYAML（运行环境未安装），请改用 K8S_API_URL/K8S_TOKEN"
            try:
                with open(kc, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                cur = (cfg.get("current-context") or "")
                ctx = next((c for c in cfg.get("contexts", []) if c.get("name") == cur), None) or (
                    cfg.get("contexts") or [{}])[0]
                cluster = next((c for c in cfg.get("clusters", []) if c.get("name") == (ctx.get("context") or {}).get("cluster")), {})
                user = next((u for u in cfg.get("users", []) if u.get("name") == (ctx.get("context") or {}).get("user")), {})
                cu, cc = user.get("user") or {}, cluster.get("cluster") or {}
                url = cc.get("server") or ""
                tok = cu.get("token") or ""
                if not tok and cu.get("token-file"):
                    with open(cu["token-file"], "r", encoding="utf-8") as f:
                        tok = f.read().strip()
                if not tok and cu.get("client-certificate-data") and cu.get("client-key-data"):
                    # 客户端证书模式暂不支持（避免把证书读进内存链），明确降级提示
                    return "none", "", "", "kubeconfig 使用客户端证书认证，暂不支持；请改用 token 模式"
                verify_path = cc.get("certificate-authority")
                if not verify_path and cc.get("certificate-authority-data"):
                    verify_path = self._temp_ca_file(cc["certificate-authority-data"])
                if not url or not tok:
                    return "none", "", "", "kubeconfig 缺少 server 或 token"
                return "kubeconfig", url, tok, (False if insecure_flag else verify_path)
            except Exception as exc:
                return "none", "", "", sanitize_error(exc)
        return "none", "", "", ""

    def _temp_ca_file(self, b64data: str) -> str:
        import tempfile
        path = os.path.join(tempfile.gettempdir(), f"opscenter-k8s-ca-{os.getpid()}.crt")
        with open(path, "wb") as f:
            f.write(base64.b64decode(b64data))
        return path

    # ── 状态 ─────────────────────────────────────────────────
    def status(self) -> dict:
        ok = self._client is not None and self.mode in ("env", "in_cluster", "kubeconfig")
        return {
            "mode": self.mode,
            "ok": bool(ok),
            "error": "" if ok else (self._last_error or "未配置 Kubernetes 连接（K8S_API_URL/K8S_TOKEN 或 in_cluster）"),
            "base_url": self.base_url or "",
        }

    # ── 核心请求（GET 专用） ─────────────────────────────────
    def get_json(self, path: str, params: Optional[dict] = None) -> Any:
        if self._client is None:
            raise K8sError("Kubernetes 客户端未初始化")
        last_exc: Optional[Exception] = None
        for attempt in (0, 1):  # 有界重试：仅连接错误/5xx，最多 1 次
            try:
                with self._sem:
                    resp = self._client.get(path, params=params)
                if resp.status_code == 404:
                    raise K8sError("资源不存在（404）", status=404)
                if resp.status_code >= 400:
                    detail = ""
                    try:
                        body = resp.json()
                        detail = str(body.get("message") or "")
                    except Exception:
                        detail = (resp.text or "")[:200]
                    raise K8sError(f"K8s API {resp.status_code}: {detail}"[:300], status=resp.status_code)
                return resp.json()
            except K8sError:
                raise
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.PoolTimeout) as exc:
                last_exc = exc
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                raise K8sError(f"连接 Kubernetes 失败: {sanitize_error(exc)}") from exc
            except httpx.HTTPStatusError as exc:  # 5xx
                last_exc = exc
                if attempt == 0 and exc.response is not None and exc.response.status_code >= 500:
                    time.sleep(0.5)
                    continue
                raise K8sError(f"K8s API 错误: {sanitize_error(exc)}") from exc
            except Exception as exc:
                raise K8sError(f"K8s 请求失败: {sanitize_error(exc)}") from exc
        raise K8sError(f"K8s 请求失败: {sanitize_error(last_exc)}")

    # ── 便捷列表方法（全 GET） ───────────────────────────────
    @staticmethod
    def _items(payload: Any) -> list:
        if isinstance(payload, dict):
            return payload.get("items") or []
        if isinstance(payload, list):
            return payload
        return []

    def version(self) -> dict:
        return self.get_json("/version")

    def list_nodes(self) -> list:
        return self.get_json("/api/v1/nodes")

    def list_namespaces(self) -> list:
        return self.get_json("/api/v1/namespaces")

    def _workload_path(self, kind: str) -> str:
        # 兼容单/复数写法（调用方契约：BE-3 用复数，k8s_monitor 用单数）
        k = (kind or "").lower().rstrip("s")
        return {
            "deployment": "/apis/apps/v1/deployments",
            "statefulset": "/apis/apps/v1/statefulsets",
            "daemonset": "/apis/apps/v1/daemonsets",
        }[k]

    def list_workloads(self, kind: str, namespace: Optional[str] = None) -> list:
        base = self._workload_path(kind)
        path = f"{base.rsplit('/', 1)[0]}/namespaces/{namespace}/{base.rsplit('/', 1)[1]}" if namespace else base
        return self.get_json(path)

    def list_pods(self, namespace: Optional[str] = None, field_selector: Optional[str] = None,
                  label_selector: Optional[str] = None) -> list:
        path = f"/api/v1/namespaces/{namespace}/pods" if namespace else "/api/v1/pods"
        params = {}
        if field_selector:
            params["fieldSelector"] = field_selector
        if label_selector:
            params["labelSelector"] = label_selector
        return self.get_json(path, params=params or None)

    def get_pod(self, namespace: str, name: str) -> dict:
        return self.get_json(f"/api/v1/namespaces/{namespace}/pods/{name}")

    def list_services(self, namespace: Optional[str] = None) -> list:
        path = f"/api/v1/namespaces/{namespace}/services" if namespace else "/api/v1/services"
        return self.get_json(path)

    def list_endpoints(self, namespace: Optional[str] = None) -> list:
        path = f"/api/v1/namespaces/{namespace}/endpoints" if namespace else "/api/v1/endpoints"
        return self.get_json(path)

    def list_ingresses(self, namespace: Optional[str] = None) -> list:
        base = "/apis/networking.k8s.io/v1/ingresses"
        path = f"{base.rsplit('/', 1)[0]}/namespaces/{namespace}/{base.rsplit('/', 1)[1]}" if namespace else base
        return self.get_json(path)

    def list_pv(self) -> list:
        return self.get_json("/api/v1/persistentvolumes")

    def list_pvcs(self, namespace: Optional[str] = None) -> list:
        path = (f"/api/v1/namespaces/{namespace}/persistentvolumeclaims"
                if namespace else "/api/v1/persistentvolumeclaims")
        return self.get_json(path)

    def list_jobs(self, namespace: Optional[str] = None) -> list:
        base = "/apis/batch/v1/jobs"
        path = f"{base.rsplit('/', 1)[0]}/namespaces/{namespace}/{base.rsplit('/', 1)[1]}" if namespace else base
        return self.get_json(path)

    def list_cronjobs(self, namespace: Optional[str] = None) -> list:
        base = "/apis/batch/v1/cronjobs"
        path = f"{base.rsplit('/', 1)[0]}/namespaces/{namespace}/{base.rsplit('/', 1)[1]}" if namespace else base
        return self.get_json(path)

    def list_events(self, namespace: Optional[str] = None, field_selector: Optional[str] = None) -> list:
        path = f"/api/v1/namespaces/{namespace}/events" if namespace else "/api/v1/events"
        params = {"fieldSelector": field_selector} if field_selector else None
        return self.get_json(path, params=params)

    def list_network_policies(self, namespace: Optional[str] = None) -> list:
        base = "/apis/networking.k8s.io/v1/networkpolicies"
        path = f"{base.rsplit('/', 1)[0]}/namespaces/{namespace}/{base.rsplit('/', 1)[1]}" if namespace else base
        return self.get_json(path)

    def node_metrics(self) -> list:
        return self.get_json("/apis/metrics.k8s.io/v1beta1/nodes")

    def pod_metrics(self, namespace: Optional[str] = None) -> list:
        path = (f"/apis/metrics.k8s.io/v1beta1/namespaces/{namespace}/pods"
                if namespace else "/apis/metrics.k8s.io/v1beta1/pods")
        return self.get_json(path)

    def pod_log(self, namespace: str, name: str, container: Optional[str] = None,
                tail_lines: int = 300, since_seconds: Optional[int] = None) -> str:
        if not valid_k8s_name(namespace) or not valid_k8s_name(name):
            raise K8sError("非法 namespace/pod 名称", status=422)
        params: dict = {"tailLines": int(tail_lines)}
        if container:
            params["container"] = container
        if since_seconds:
            params["sinceSeconds"] = int(since_seconds)
        try:
            with self._sem:
                resp = self._client.get(f"/api/v1/namespaces/{namespace}/pods/{name}/log", params=params)
            if resp.status_code >= 400:
                try:
                    detail = str(resp.json().get("message") or "")
                except Exception:
                    detail = (resp.text or "")[:200]
                raise K8sError(f"日志获取失败 {resp.status_code}: {detail}"[:300], status=resp.status_code)
            return resp.text or ""
        except K8sError:
            raise
        except Exception as exc:
            raise K8sError(f"日志获取失败: {sanitize_error(exc)}") from exc


# ── 模块级单例（供 topology.py / server_details.py 接缝） ─────
_client_lock = threading.Lock()
_client: Optional[K8sClient] = None


def get_client() -> Optional[K8sClient]:
    """懒加载单例。配置缺失时也返回实例（mode='none'，status().ok=False），
    让调用方可以读取 mode 做降级判断；仅构造异常时返回 None。"""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                try:
                    _client = K8sClient()
                except Exception:
                    return None
    return _client


def reset_client() -> None:
    global _client
    with _client_lock:
        if _client is not None:
            try:
                _client._client.close()
            except Exception:
                pass
        _client = None


def list_nodes() -> list:
    """模块级便捷方法（server_details 接缝）：返回原始节点 items 列表。"""
    client = get_client()
    if client is None or not client.status()["ok"]:
        raise K8sError("Kubernetes 未配置或不可用")
    return K8sClient._items(client.list_nodes())


def parse_cpu(value: Any) -> Optional[float]:
    """K8s CPU quantity → 核数（float）。非法返回 None。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("m"):
            return float(text[:-1]) / 1000.0
        if text.endswith("u"):
            return float(text[:-1]) / 1_000_000.0
        if text.endswith("n"):
            return float(text[:-1]) / 1_000_000_000.0
        return float(text)
    except ValueError:
        return None


_MEM_UNITS = {"Ki": 1024.0, "Mi": 1024.0 ** 2, "Gi": 1024.0 ** 3, "Ti": 1024.0 ** 4,
              "K": 1000.0, "M": 1000.0 ** 2, "G": 1000.0 ** 3, "T": 1000.0 ** 3 * 1.0}


def parse_memory(value: Any) -> Optional[float]:
    """K8s memory quantity → 字节（float）。非法返回 None。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        for suffix, mult in _MEM_UNITS.items():
            if text.endswith(suffix):
                return float(text[: -len(suffix)]) * mult
        return float(text)
    except ValueError:
        return None
