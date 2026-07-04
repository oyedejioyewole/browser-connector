"""Priority acquisition queue, capacity, preemption, and timeouts.

These run against a browser-free pool (see conftest.make_pool). asyncio_mode is
"auto" (pyproject), so async test functions run under pytest-asyncio directly.
"""

from __future__ import annotations

import asyncio
import heapq

import pytest
from conftest import make_pool, make_settings

from camoufox_connector.pool import _Waiter


async def test_acquire_and_release_roundtrip():
    pool = make_pool(n=2)
    lease = await pool.acquire(priority=0, timeout=1)
    assert lease is not None
    assert lease.endpoint.startswith("ws://")
    assert pool.instances[lease.instance_index].active_leases == 1

    result = await pool.release(lease.lease_id)
    assert result == {"released": True, "preempted": False}
    assert pool.instances[lease.instance_index].active_leases == 0


async def test_release_unknown_lease():
    pool = make_pool(n=1)
    result = await pool.release("does-not-exist")
    assert result["released"] is False


async def test_capacity_is_pool_size_times_per_instance():
    settings = make_settings(pool_size=2, max_concurrency_per_instance=2)
    pool = make_pool(settings=settings, n=2)
    leases = [await pool.acquire(timeout=0.5) for _ in range(4)]
    assert all(x is not None for x in leases)  # 2 instances * 2 capacity
    # Fifth acquire has no capacity and no wait -> None.
    assert await pool.acquire(timeout=0) is None


async def test_no_wait_returns_none_when_busy():
    pool = make_pool(n=1)
    first = await pool.acquire(timeout=0)
    assert first is not None
    assert await pool.acquire(timeout=0) is None


async def test_timeout_returns_none_and_reaps_waiter():
    pool = make_pool(n=1)
    held = await pool.acquire(timeout=0)  # occupy the only instance
    result = await pool.acquire(priority=0, timeout=0.15)
    assert result is None
    # A subsequent dispatch must actually reap the cancelled waiter from the heap
    # (not just flag it), so the queue can't grow unbounded.
    await pool.release(held.lease_id)
    assert len(pool._waiters) == 0
    assert pool.get_stats()["leases"]["waiting"] == 0


async def test_cancel_waiter_reclaims_raced_lease():
    """The subtle path: acquire() times out at the instant its waiter was granted
    a lease. _cancel_waiter_locked must return that lease, not leak capacity."""
    pool = make_pool(n=1)
    held = await pool.acquire(timeout=0)  # 1/1 full
    loop = asyncio.get_running_loop()
    waiter = _Waiter(sort_key=(0, 0), priority=0, future=loop.create_future())
    async with pool._lock:
        heapq.heappush(pool._waiters, waiter)
    # Free the slot -> dispatch grants a lease onto waiter.future.
    await pool.release(held.lease_id)
    assert waiter.future.done()
    # Now the "timeout lost the race" cleanup runs; the lease must be returned.
    async with pool._lock:
        pool._cancel_waiter_locked(waiter)
    assert pool.instances[0].active_leases == 0
    assert pool._leases == {}


async def test_higher_priority_served_first_on_release():
    pool = make_pool(n=2)
    l1 = await pool.acquire(priority=0, timeout=1)
    l2 = await pool.acquire(priority=0, timeout=1)  # pool now full (2/2)

    # Two queued acquirers; the higher-priority one must win the first freed slot
    # regardless of arrival order.
    low = asyncio.create_task(pool.acquire(priority=1, timeout=2))
    high = asyncio.create_task(pool.acquire(priority=9, timeout=2))
    await asyncio.sleep(0.05)  # let both enqueue
    assert not low.done() and not high.done()

    await pool.release(l1.lease_id)
    await asyncio.sleep(0.02)
    assert high.done() and not low.done()

    await pool.release(l2.lease_id)
    await asyncio.sleep(0.02)
    assert low.done()

    assert (await high).priority == 9
    assert (await low).priority == 1


async def test_fifo_within_same_priority():
    pool = make_pool(n=1)
    held = await pool.acquire(priority=0, timeout=1)

    first = asyncio.create_task(pool.acquire(priority=5, timeout=2))
    await asyncio.sleep(0.02)
    second = asyncio.create_task(pool.acquire(priority=5, timeout=2))
    await asyncio.sleep(0.02)

    await pool.release(held.lease_id)
    await asyncio.sleep(0.02)
    # Same priority -> the one that queued first is served first.
    assert first.done() and not second.done()
    await pool.release((await first).lease_id)
    await asyncio.sleep(0.02)
    assert second.done()


async def test_preemption_frees_slot_after_grace():
    settings = make_settings(
        pool_size=1, preemption=True, preempt_grace=0.1, restart_on_preempt=False
    )
    pool = make_pool(settings=settings, n=1)

    low = await pool.acquire(priority=0, timeout=1)
    assert low is not None
    before_proc = pool.instances[0].process

    high = asyncio.create_task(pool.acquire(priority=5, timeout=2))
    await asyncio.sleep(0.02)
    # The low-priority lease is flagged preempted immediately.
    assert pool.get_lease(low.lease_id)["preempted"] is True

    # After the grace period, the slot is reclaimed and the high-priority
    # acquirer is served.
    got = await asyncio.wait_for(high, timeout=2)
    assert got.priority == 5
    assert pool.get_lease(low.lease_id) is None
    # restart_on_preempt=False -> the slot was freed WITHOUT restarting the browser.
    assert pool.instances[0].process is before_proc


async def test_cooperative_release_within_grace_cancels_reclaim():
    settings = make_settings(
        pool_size=1, preemption=True, preempt_grace=5.0, restart_on_preempt=False
    )
    pool = make_pool(settings=settings, n=1)

    low = await pool.acquire(priority=0, timeout=1)
    high = asyncio.create_task(pool.acquire(priority=5, timeout=5))
    await asyncio.sleep(0.02)
    assert pool.get_lease(low.lease_id)["preempted"] is True

    # Holder yields voluntarily well within the (long) grace window.
    result = await pool.release(low.lease_id)
    assert result["preempted"] is True
    got = await asyncio.wait_for(high, timeout=1)
    assert got.priority == 5


async def test_preemption_with_restart_gives_clean_instance():
    settings = make_settings(
        pool_size=1, preemption=True, preempt_grace=0.1, restart_on_preempt=True
    )
    pool = make_pool(settings=settings, n=1)

    low = await pool.acquire(priority=0, timeout=1)
    before_proc = pool.instances[0].process
    high = asyncio.create_task(pool.acquire(priority=5, timeout=3))
    got = await asyncio.wait_for(high, timeout=3)
    assert got.priority == 5
    assert got.endpoint.startswith("ws://")
    assert pool.instances[0].is_healthy is True
    assert pool.get_lease(low.lease_id) is None
    # restart_on_preempt=True -> the browser process was actually replaced (proving
    # a real restart happened, not just a slot re-handout).
    assert pool.instances[0].process is not before_proc


async def test_preemptor_timeout_spares_victim():
    """If the preemptor gives up before the grace expires and nobody else is
    waiting, the preempted victim must NOT be reclaimed/restarted."""
    settings = make_settings(
        pool_size=1, preemption=True, preempt_grace=5.0, restart_on_preempt=True
    )
    pool = make_pool(settings=settings, n=1)

    low = await pool.acquire(priority=0, timeout=1)
    before_proc = pool.instances[0].process

    # High-priority acquire with a short timeout < grace: it preempts low, then
    # times out and leaves.
    result = await pool.acquire(priority=5, timeout=0.1)
    assert result is None

    await asyncio.sleep(0.05)
    lease_obj = pool._leases.get(low.lease_id)
    assert lease_obj is not None                 # victim still holds its lease
    assert lease_obj.preempted.is_set() is False  # un-flagged (spared)
    assert lease_obj.reclaim_task is None         # pending reclaim cancelled
    assert pool.instances[0].process is before_proc  # never restarted


async def test_preemption_preserves_sibling_lease_high_capacity():
    """Capacity>1: preempting one lease must not restart the instance and kill an
    unrelated sibling lease sharing it."""
    settings = make_settings(
        pool_size=1,
        max_concurrency_per_instance=2,
        preemption=True,
        preempt_grace=0.1,
        restart_on_preempt=True,
    )
    pool = make_pool(settings=settings, n=1)

    sibling = await pool.acquire(priority=5, timeout=1)  # higher-priority sibling
    low = await pool.acquire(priority=0, timeout=1)      # victim, same instance (2/2)
    before_proc = pool.instances[0].process

    high = asyncio.create_task(pool.acquire(priority=9, timeout=3))
    got = await asyncio.wait_for(high, timeout=3)
    assert got.priority == 9
    assert pool.get_lease(low.lease_id) is None            # low was reclaimed
    assert pool.get_lease(sibling.lease_id) is not None    # sibling survived
    assert pool.instances[0].process is before_proc        # instance NOT restarted


async def test_no_preemption_of_equal_or_higher_priority():
    settings = make_settings(
        pool_size=1, preemption=True, preempt_grace=0.1, restart_on_preempt=False
    )
    pool = make_pool(settings=settings, n=1)

    held = await pool.acquire(priority=5, timeout=1)
    # A lower/equal priority request must NOT preempt the higher-priority holder.
    result = await pool.acquire(priority=5, timeout=0.15)
    assert result is None
    assert pool.get_lease(held.lease_id)["preempted"] is False


async def test_acquire_cancellation_propagates():
    """A cancelled acquire() must re-raise CancelledError (not swallow it) and
    still clean up its waiter."""
    pool = make_pool(n=1)
    await pool.acquire(timeout=0)  # fill the only instance
    task = asyncio.create_task(pool.acquire(priority=0, timeout=30))
    await asyncio.sleep(0.02)  # let it enqueue
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with pool._lock:
        assert all(w.cancelled for w in pool._waiters) or not pool._waiters


async def test_max_waiters_bounds_the_queue():
    settings = make_settings(pool_size=1, max_waiters=2)
    pool = make_pool(settings=settings, n=1)
    await pool.acquire(timeout=0)  # fill the instance

    w1 = asyncio.create_task(pool.acquire(timeout=5))
    w2 = asyncio.create_task(pool.acquire(timeout=5))
    await asyncio.sleep(0.02)  # both enqueued -> at the cap of 2
    # A third acquire is refused immediately rather than growing the queue.
    assert await pool.acquire(timeout=5) is None

    for t in (w1, w2):
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t


async def test_acquire_none_when_no_healthy_instances():
    pool = make_pool(n=1)
    pool.instances[0].is_healthy = False
    assert await pool.acquire(timeout=0.1) is None


async def test_restart_drops_leases_on_instance():
    pool = make_pool(n=1)
    lease = await pool.acquire(timeout=1)
    assert pool.instances[0].active_leases == 1
    ok = await pool.restart_instance(0)
    assert ok is True
    assert pool.instances[0].active_leases == 0
    assert pool.get_lease(lease.lease_id) is None


async def test_health_check_detects_died_process():
    pool = make_pool(n=1)
    assert pool.instances[0].is_healthy is True
    pool.instances[0].process.returncode = 1  # simulate the browser crashing
    result = await pool.health_check()
    assert pool.instances[0].is_healthy is False
    assert result["healthy"] is False


async def test_next_endpoint_unaffected_by_leases():
    """/next round-robin must keep working independently of the lease system."""
    pool = make_pool(n=2)
    await pool.acquire(timeout=1)  # take a lease
    # /next still hands out endpoints regardless of lease state.
    e1 = await pool.get_next_endpoint()
    e2 = await pool.get_next_endpoint()
    assert e1 and e2 and e1 != e2
