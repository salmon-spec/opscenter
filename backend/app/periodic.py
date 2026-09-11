"""Run long-lived background jobs under an optional Redis lease."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from app.services.redis_store import redis_store


async def run_periodic_job(
    name: str,
    factory: Callable[[], Awaitable[object]],
    *,
    leader_enabled: bool,
    lease_seconds: int = 45,
    renew_seconds: int = 15,
    retry_seconds: int = 5,
) -> object | None:
    """Run one job, cancelling it promptly if this process loses its lease.

    With leader election disabled this is a transparent wrapper. When enabled,
    Redis is fail-closed: no lease means no job. API traffic remains available.
    """
    if not leader_enabled:
        return await factory()

    lease_name = f"leader:periodic:{name}"
    while True:
        owns_lease = await asyncio.to_thread(redis_store.leader, lease_name, lease_seconds)
        if not owns_lease:
            await asyncio.sleep(retry_seconds)
            continue

        child = asyncio.create_task(factory(), name=f"periodic:{name}")
        try:
            while True:
                done, _ = await asyncio.wait({child}, timeout=renew_seconds)
                if done:
                    return await child
                owns_lease = await asyncio.to_thread(redis_store.leader, lease_name, lease_seconds)
                if not owns_lease:
                    child.cancel()
                    await asyncio.gather(child, return_exceptions=True)
                    break
        except asyncio.CancelledError:
            child.cancel()
            await asyncio.gather(child, return_exceptions=True)
            raise
