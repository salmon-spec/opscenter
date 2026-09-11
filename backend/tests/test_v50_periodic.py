import asyncio

from app import periodic


def test_periodic_wrapper_is_transparent_when_disabled():
    calls = []

    async def job():
        calls.append("ran")
        return 42

    result = asyncio.run(periodic.run_periodic_job(
        "plain", job, leader_enabled=False,
    ))
    assert result == 42
    assert calls == ["ran"]


def test_periodic_job_waits_for_lease(monkeypatch):
    attempts = iter([False, False, True])
    monkeypatch.setattr(periodic.redis_store, "leader", lambda *_args: next(attempts))
    calls = []

    async def job():
        calls.append("ran")
        return "done"

    result = asyncio.run(periodic.run_periodic_job(
        "elected", job, leader_enabled=True, retry_seconds=0,
    ))
    assert result == "done"
    assert calls == ["ran"]


def test_periodic_job_cancels_child_after_losing_lease(monkeypatch):
    lease_calls = 0
    child_cancelled = asyncio.Event()

    def leader(*_args):
        nonlocal lease_calls
        lease_calls += 1
        return lease_calls == 1

    monkeypatch.setattr(periodic.redis_store, "leader", leader)

    async def job():
        try:
            await asyncio.Event().wait()
        finally:
            child_cancelled.set()

    async def scenario():
        task = asyncio.create_task(periodic.run_periodic_job(
            "lease-loss", job, leader_enabled=True,
            renew_seconds=0.01, retry_seconds=0.01,
        ))
        await asyncio.wait_for(child_cancelled.wait(), timeout=1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert lease_calls >= 2
