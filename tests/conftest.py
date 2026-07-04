"""Shared test fixtures.

The tests never launch a real Camoufox browser: they build a BrowserPool whose
instances are pre-marked healthy with fake WebSocket endpoints, and neuter the
process-management methods. This keeps the suite fast, deterministic, and
runnable in CI without the browser binary.
"""

from __future__ import annotations

from camoufox_connector.config import Settings
from camoufox_connector.pool import BrowserInstance, BrowserPool
from camoufox_connector.proxy import ProxyPool


class _FakeProc:
    """Stand-in for asyncio.subprocess.Process so health_check sees a live proc."""

    returncode = None


def make_settings(**overrides) -> Settings:
    """Settings with geoip off (no proxy needed) and pool mode by default."""
    data = {"mode": "pool", "pool_size": 3, "geoip": False}
    data.update(overrides)
    return Settings(**data)


def make_pool(settings: Settings | None = None, n: int | None = None) -> BrowserPool:
    """Build a BrowserPool with `n` healthy fake instances and no real processes."""
    if settings is None:
        settings = make_settings(pool_size=n or 3)
    if n is None:
        n = 1 if settings.mode.value == "single" else settings.pool_size

    pool = BrowserPool(settings=settings)
    pool._proxy_pool = ProxyPool(settings.proxy_list)

    for i in range(n):
        inst = BrowserInstance(
            index=i,
            port=settings.get_ws_port(i),
            ws_endpoint=f"ws://127.0.0.1:{settings.get_ws_port(i)}/fake{i}",
            is_healthy=True,
            proxy=pool._proxy_pool.assign(i),
        )
        inst.process = _FakeProc()
        inst.started_at = 1.0
        pool.instances.append(inst)

    # Replace real subprocess management with instant fakes so restart_instance
    # works in tests without launching anything.
    async def _fake_start(instance: BrowserInstance) -> None:
        instance.is_healthy = True
        instance.ws_endpoint = f"ws://127.0.0.1:{instance.port}/fake{instance.index}"
        instance.process = _FakeProc()
        instance.started_at = 1.0

    async def _fake_stop(instance: BrowserInstance) -> None:
        instance.is_healthy = False
        instance.ws_endpoint = None

    pool._start_instance = _fake_start  # type: ignore[assignment]
    pool._stop_instance = _fake_stop  # type: ignore[assignment]
    return pool
