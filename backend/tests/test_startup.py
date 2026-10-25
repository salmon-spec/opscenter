import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from app import main


def test_startup_retries_a_transient_database_failure(monkeypatch):
    create = Mock(side_effect=[RuntimeError("database starting"), None])
    sleep = AsyncMock()
    monkeypatch.setattr(main.Base.metadata, "create_all", create)
    for name in ("_ensure_new_columns", "_migrate_legacy_service_credentials", "apply_persisted_configs", "seed_extension_modules"):
        monkeypatch.setattr(main, name, Mock())
    monkeypatch.setattr(main, "PREVIEW_MODE", True)
    monkeypatch.setattr(main.asyncio, "sleep", sleep)

    asyncio.run(main.startup())

    assert create.call_count == 2
    sleep.assert_awaited_once_with(2)


def test_startup_stops_when_database_retries_are_exhausted(monkeypatch):
    create = Mock(side_effect=RuntimeError("database unavailable"))
    sleep = AsyncMock()
    monkeypatch.setattr(main.Base.metadata, "create_all", create)
    monkeypatch.setattr(main, "PREVIEW_MODE", True)
    monkeypatch.setattr(main.asyncio, "sleep", sleep)

    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(main.startup())

    assert create.call_count == 30
    assert sleep.await_count == 29
