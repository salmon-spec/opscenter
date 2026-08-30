"""OpsCenter Docker 镜像更新检测（v3.27, D4）。

设计：后端每日一次拉取各服务器 Agent 的 /api/v1/images（默认本地模式，
registry 对比由 Agent 端可选），结果 upsert 到 image_status 表。
IMAGE_CHECK_ENABLED=false 一键关停（回滚兜底）。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from app.config import IMAGE_CHECK_ENABLED
from app.database import get_db
from app.models import ImageStatus, Server

logger = logging.getLogger("opscenter.images")


def _agent_images(server: Server, timeout: float = 15.0):
    """调用远端 Agent 的 images 端点。"""
    import requests
    host = "127.0.0.1" if server.agent_type == "local" else server.host
    port = server.agent_port or 19100
    token = server.agent_token or ""
    url = f"http://{host}:{port}/api/v1/images"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return (r.json().get("images") or [])


def run_image_check() -> None:
    """采集一轮所有服务器的容器镜像，upsert image_status。"""
    if not IMAGE_CHECK_ENABLED:
        return
    with get_db() as db:
        servers = db.query(Server).filter(Server.enabled == True, Server.agent_status == "running").all()  # noqa: E712
        for server in servers:
            try:
                images = _agent_images(server)
            except Exception as e:
                logger.warning("image check failed on %s: %s", server.name, e)
                continue
            for img in images:
                existing = (
                    db.query(ImageStatus)
                    .filter(
                        ImageStatus.server_id == server.id,
                        ImageStatus.container_name == img.get("container_name", ""),
                    )
                    .first()
                )
                if existing:
                    existing.image = img.get("image")
                    existing.local_digest = img.get("local_digest")
                    existing.remote_digest = img.get("remote_digest")
                    existing.outdated = bool(img.get("outdated"))
                    existing.checked_at = datetime.utcnow()
                else:
                    db.add(ImageStatus(
                        server_id=server.id,
                        container_name=img.get("container_name", ""),
                        image=img.get("image", ""),
                        local_digest=img.get("local_digest"),
                        remote_digest=img.get("remote_digest"),
                        outdated=bool(img.get("outdated")),
                    ))
            db.commit()
            logger.info("image check %s: %d images", server.name, len(images))


async def image_check_loop() -> None:
    """后台任务：每日 03:30 一轮镜像检查。"""
    while True:
        now = datetime.utcnow()
        nxt = now.replace(hour=3, minute=30, second=0, microsecond=0)
        if now >= nxt:
            nxt = nxt.replace(day=nxt.day + 1)
        await asyncio.sleep((nxt - now).total_seconds())
        try:
            await asyncio.to_thread(run_image_check)
        except Exception as e:
            logger.exception("image check loop error: %s", e)
