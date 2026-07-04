"""ProxyPool assignment, rotation, blacklisting, and credential masking."""

from __future__ import annotations

from camoufox_connector.proxy import ProxyPool, mask_proxy_url


def test_mask_hides_credentials():
    assert mask_proxy_url("http://user:pass@host:8080") == "http://***@host:8080"


def test_mask_leaves_credentialless_url():
    assert mask_proxy_url("http://host:8080") == "http://host:8080"


def test_mask_none():
    assert mask_proxy_url(None) is None


def test_empty_pool_is_falsy():
    pool = ProxyPool([])
    assert not pool
    assert pool.assign(0) is None
    assert pool.size == 0


def test_dedupes_and_strips():
    pool = ProxyPool(["http://a:1", " http://a:1 ", "", "http://b:2"])
    assert pool.proxies == ["http://a:1", "http://b:2"]


def test_deterministic_round_robin_assignment():
    pool = ProxyPool(["http://a:1", "http://b:2"])
    assert pool.assign(0) == "http://a:1"
    assert pool.assign(1) == "http://b:2"
    assert pool.assign(2) == "http://a:1"  # wraps
    assert pool.assign(3) == "http://b:2"


def test_assignment_recorded():
    pool = ProxyPool(["http://a:1", "http://b:2"])
    pool.assign(0)
    pool.assign(1)
    assert pool.assigned(0) == "http://a:1"
    assert pool.assigned(1) == "http://b:2"


def test_blacklist_removes_from_available():
    pool = ProxyPool(["http://a:1", "http://b:2"])
    pool.blacklist("http://a:1")
    assert pool.available() == ["http://b:2"]


def test_assign_skips_blacklisted():
    pool = ProxyPool(["http://a:1", "http://b:2"])
    pool.blacklist("http://a:1")
    # Only b is available, so every index maps to b.
    assert pool.assign(0) == "http://b:2"
    assert pool.assign(1) == "http://b:2"


def test_assign_falls_back_when_all_blacklisted():
    pool = ProxyPool(["http://a:1", "http://b:2"])
    pool.blacklist("http://a:1")
    pool.blacklist("http://b:2")
    # Rather than starve the instance, fall back to the full list.
    assert pool.assign(0) in ("http://a:1", "http://b:2")


def test_rotate_prefers_unused_proxy():
    pool = ProxyPool(["http://a:1", "http://b:2", "http://c:3"])
    pool.assign(0)  # a
    pool.assign(1)  # b
    # Rotating instance 0 should prefer c (not used by instance 1, not current a).
    assert pool.rotate(0) == "http://c:3"
    assert pool.assigned(0) == "http://c:3"


def test_rotate_with_blacklist_current():
    pool = ProxyPool(["http://a:1", "http://b:2"])
    pool.assign(0)  # a
    new = pool.rotate(0, blacklist_current=True)
    assert new == "http://b:2"
    assert "http://a:1" not in pool.available()


def test_rotate_avoids_returning_current_when_possible():
    pool = ProxyPool(["http://a:1", "http://b:2"])
    pool.assign(0)  # a
    assert pool.rotate(0) == "http://b:2"


def test_to_dict_masks_credentials():
    pool = ProxyPool(["http://user:pass@a:1", "http://user:pass@b:2"])
    pool.assign(0)
    pool.blacklist("http://user:pass@b:2")
    d = pool.to_dict()
    assert d["total"] == 2
    assert d["assignments"]["0"] == "http://***@a:1"
    assert d["blacklisted"] == ["http://***@b:2"]
