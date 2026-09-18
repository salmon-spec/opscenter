"""Small, read-only OpenCode Go client used by the AI context API."""
from __future__ import annotations

import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import requests


class OpenCodeError(RuntimeError):
    """An upstream/configuration failure safe to expose to an API client."""

    def __init__(self, code: str, message: str, status_code: int = 502):
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.status_code = status_code


def _safe_base_url(value: str) -> str:
    """Allow only a plain HTTP(S) endpoint; never carry URL credentials/query data."""
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            return ""
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        netloc = f"{host}:{parsed.port}" if parsed.port else host
        return urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", ""))
    except (TypeError, ValueError):
        return ""


def _config() -> dict[str, str | float]:
    # Keep the key lookup compatible with the existing ASR/OpenCode Go setup.
    api_key = (os.getenv("OPENCODE_API_KEY") or os.getenv("OPENCODE_GO_API_KEY") or "").strip()
    raw_timeout = os.getenv("OPENCODE_TIMEOUT_SECONDS", "45")
    try:
        timeout = max(5.0, min(float(raw_timeout), 180.0))
    except (TypeError, ValueError):
        timeout = 45.0
    return {
        "api_key": api_key,
        "base_url": _safe_base_url(
            os.getenv("OPENCODE_BASE_URL") or "https://opencode.ai/zen/go/v1"
        ),
        "model": (os.getenv("OPENCODE_MODEL") or "deepseek-v4.1-flash").strip(),
        "timeout": timeout,
    }


def provider_status() -> dict[str, str | bool]:
    config = _config()
    return {
        "provider": "opencode-go",
        "configured": bool(config["api_key"]),
        "base_url": str(config["base_url"]),
        "model": str(config["model"]),
        "read_only": True,
    }


def chat(messages: list[dict[str, str]], *, temperature: float = 0.2, max_tokens: int = 1200) -> dict:
    """Call the OpenAI-compatible endpoint without logging or returning credentials."""
    config = _config()
    if not config["base_url"]:
        raise OpenCodeError("invalid_config", "OpenCode 服务地址配置无效", 503)
    if not config["api_key"]:
        raise OpenCodeError("not_configured", "OpenCode 模型尚未配置测试环境密钥", 503)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config['api_key']}",
        "User-Agent": "opscenter-ai/1.0",
        # OpenCode Go requires a session marker; it contains no user secret.
        "x-opencode-session": f"opscenter-ai-{uuid.uuid4().hex}",
    }
    payload = {
        "model": config["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        # Command Code exposes the OpenAI-compatible JSON mode.  Keep the
        # diagnosis contract machine-readable instead of repairing arbitrary
        # model prose after the fact.
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    try:
        response = requests.post(
            f"{config['base_url']}/chat/completions",
            headers=headers,
            json=payload,
            timeout=config["timeout"],
        )
    except requests.RequestException as exc:
        # Do not include exception text: some HTTP clients may include headers.
        raise OpenCodeError("upstream_unreachable", "OpenCode 模型服务暂时不可达", 502) from exc

    if response.status_code >= 400:
        raise OpenCodeError("upstream_error", f"OpenCode 模型请求失败（HTTP {response.status_code}）", 502)
    try:
        data = response.json()
        choice = data["choices"][0]
        content = choice["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise OpenCodeError("invalid_response", "OpenCode 模型返回格式无效", 502) from exc

    if isinstance(content, list):
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    content = str(content or "").strip()
    if not content:
        raise OpenCodeError("empty_response", "OpenCode 模型返回空内容", 502)
    return {
        "content": content,
        "model": str(data.get("model") or config["model"]),
        "request_id": str(data.get("id") or ""),
        "usage": data.get("usage") if isinstance(data.get("usage"), dict) else None,
    }

