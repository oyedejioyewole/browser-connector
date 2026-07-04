"""HTTP API: new /acquire, /release, /lease routes; /next and /restart behavior.

Uses an in-loop ASGI client (httpx.ASGITransport) rather than the sync
TestClient so the BrowserPool and the request handlers share a single event
loop on every supported Python version (on 3.9 an asyncio.Lock binds to its loop
at construction, which a separate-loop TestClient would trip over).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from conftest import make_pool, make_settings
from httpx import ASGITransport, AsyncClient

from camoufox_connector.health import create_health_app


@asynccontextmanager
async def make_client(pool):
    transport = ASGITransport(app=create_health_app(pool))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def test_next_unchanged():
    pool = make_pool(n=2)
    async with make_client(pool) as client:
        r = await client.get("/next")
        assert r.status_code == 200
        assert r.json()["endpoint"].startswith("ws://")


async def test_acquire_release_lease_lifecycle():
    pool = make_pool(n=2)
    async with make_client(pool) as client:
        r = await client.get("/acquire?priority=3")
        assert r.status_code == 200
        body = r.json()
        lease_id = body["lease_id"]
        assert body["priority"] == 3
        assert body["endpoint"].startswith("ws://")

        r = await client.get(f"/lease/{lease_id}")
        assert r.status_code == 200
        assert r.json()["preempted"] is False

        r = await client.post(f"/release/{lease_id}")
        assert r.status_code == 200
        assert r.json()["released"] is True

        r = await client.get(f"/lease/{lease_id}")
        assert r.status_code == 404


async def test_release_unknown_lease_404():
    pool = make_pool(n=1)
    async with make_client(pool) as client:
        r = await client.post("/release/nope")
        assert r.status_code == 404


async def test_acquire_no_capacity_times_out():
    # Two instances, capacity 1 each -> take both, then a non-blocking acquire 503s.
    pool = make_pool(n=2)
    async with make_client(pool) as client:
        await client.get("/acquire")
        await client.get("/acquire")
        r = await client.get("/acquire?timeout=0")
        assert r.status_code == 503


async def test_acquire_bad_priority_400():
    pool = make_pool(n=2)
    async with make_client(pool) as client:
        r = await client.get("/acquire?priority=notanint")
        assert r.status_code == 400


async def test_acquire_rejects_infinite_timeout():
    pool = make_pool(n=1)
    async with make_client(pool) as client:
        r = await client.get("/acquire?timeout=inf")
        assert r.status_code == 400


async def test_acquire_rejects_negative_timeout():
    pool = make_pool(n=1)
    async with make_client(pool) as client:
        r = await client.get("/acquire?timeout=-5")
        assert r.status_code == 400


async def test_acquire_rejects_absurd_priority():
    pool = make_pool(n=1)
    async with make_client(pool) as client:
        r = await client.get("/acquire?priority=999999999999")
        assert r.status_code == 400


async def test_health_returns_503_when_all_dead():
    pool = make_pool(n=1)
    pool.instances[0].process.returncode = 1  # simulate crash
    async with make_client(pool) as client:
        r = await client.get("/health")
        assert r.status_code == 503


async def test_stats_includes_leases_and_proxies():
    pool = make_pool(n=2)
    async with make_client(pool) as client:
        r = await client.get("/stats")
        assert r.status_code == 200
        body = r.json()
        assert "leases" in body
        assert set(body["leases"]) >= {
            "active", "waiting", "capacity", "max_per_instance", "preemption",
        }
        assert "proxies" in body


async def test_health_ok():
    pool = make_pool(n=2)
    async with make_client(pool) as client:
        r = await client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"


async def test_info_reports_proxy_and_priority_config():
    pool = make_pool(n=2)
    async with make_client(pool) as client:
        r = await client.get("/")
        assert r.status_code == 200
        cfg = r.json()["config"]
        assert "proxy_count" in cfg
        assert "max_concurrency_per_instance" in cfg
        assert "preemption" in cfg


async def test_restart_with_proxy_rotation():
    settings = make_settings(pool_size=2, proxies="http://a:1,http://b:2,http://c:3")
    pool = make_pool(settings=settings, n=2)
    async with make_client(pool) as client:
        before = pool.instances[0].proxy
        r = await client.post("/restart/0?rotate_proxy=true")
        assert r.status_code == 200
        assert r.json()["proxy_rotated"] is True
        assert pool.instances[0].proxy != before  # rotated to a different proxy


async def test_restart_with_blacklist():
    settings = make_settings(pool_size=2, proxies="http://a:1,http://b:2,http://c:3")
    pool = make_pool(settings=settings, n=2)
    async with make_client(pool) as client:
        bad = pool.instances[0].proxy
        r = await client.post("/restart/0?blacklist=true")
        assert r.status_code == 200
        assert bad not in pool._proxy_pool.available()


async def test_stats_masks_proxy_credentials():
    settings = make_settings(pool_size=1, proxies="http://user:secret@host:8080")
    pool = make_pool(settings=settings, n=1)
    async with make_client(pool) as client:
        body = (await client.get("/stats")).json()
        dumped = str(body)
        assert "secret" not in dumped
        assert "***" in dumped
