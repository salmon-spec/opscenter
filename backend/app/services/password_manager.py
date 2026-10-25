"""Probe the configured vault without forwarding workbench credentials."""

from datetime import datetime, timezone
from time import monotonic
from urllib.parse import urlsplit

import requests

from app.config import _settings


def status() -> dict:
    url = _settings.vaultwarden_url.strip().rstrip("/")
    result = {
        "configured": bool(url), "available": False, "url": "", "version": "",
        "error": "", "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    if not url:
        result["error"] = "密码库尚未配置"
        return result
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"} or parsed.netloc.endswith(":")):
            raise ValueError("invalid vault URL")
        parsed.port
    except ValueError:
        result["error"] = "密码库地址必须是独立的 HTTPS 站点，且不包含账号、路径或参数"
        return result
    result["url"] = url
    started = monotonic()
    try:
        # A separate session never inherits the operator's Authorization or cookies.
        with requests.Session() as session:
            session.trust_env = False
            options = {"timeout": 4, "verify": _settings.vaultwarden_ca_file or True, "allow_redirects": False}
            response = session.get(url + "/api/config", **options)
            response.raise_for_status()
            config = response.json()
            if response.status_code != 200 or not isinstance(config, dict) or not isinstance(config.get("version"), str):
                raise ValueError("invalid vault response")
            page = session.get(url + "/", **options)
            if page.status_code != 200 or "text/html" not in page.headers.get("Content-Type", ""):
                raise ValueError("invalid vault page")
            result.update(available=True, version=config["version"][:80])
    except requests.exceptions.SSLError:
        result["error"] = "密码库证书验证失败，请检查证书有效期和信任链"
    except (requests.RequestException, ValueError, OSError):
        result["error"] = "密码库入口暂不可用，请检查服务和网络连接"
    result["latency_ms"] = round((monotonic() - started) * 1000)
    return result
