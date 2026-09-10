"""Config parsing: proxy pool list, validation, and per-instance kwargs."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from camoufox_connector.config import Settings


def _settings(**kw):
    kw.setdefault("geoip", False)
    return Settings(**kw)


def test_single_proxy_backward_compatible():
    s = _settings(proxy="http://user:pass@host:8080")
    assert s.proxy_list == ["http://user:pass@host:8080"]


def test_proxies_comma_separated_string():
    s = _settings(proxies="http://a:1, http://b:2 ,http://c:3")
    assert s.proxy_list == ["http://a:1", "http://b:2", "http://c:3"]


def test_proxies_newline_separated():
    s = _settings(proxies="http://a:1\nhttp://b:2")
    assert s.proxy_list == ["http://a:1", "http://b:2"]


def test_proxies_accepts_list_from_config():
    # JSON config files provide an actual list.
    s = _settings(proxies=["http://a:1", "http://b:2"])
    assert s.proxy_list == ["http://a:1", "http://b:2"]


def test_proxies_dedupes_and_strips_blanks():
    s = _settings(proxies="http://a:1,,http://a:1, http://b:2 ,")
    assert s.proxy_list == ["http://a:1", "http://b:2"]


def test_proxies_takes_precedence_over_single_proxy():
    s = _settings(proxy="http://single:1", proxies="http://pool-a:1,http://pool-b:2")
    assert s.proxy_list == ["http://pool-a:1", "http://pool-b:2"]


def test_empty_proxies_falls_back_to_none():
    s = _settings(proxies="")
    assert s.proxy_list == []
    assert s.proxies is None


def test_invalid_proxy_scheme_rejected():
    with pytest.raises(ValidationError):
        _settings(proxy="ftp://bad:1")


def test_invalid_proxy_in_pool_rejected():
    with pytest.raises(ValidationError):
        _settings(proxies="http://ok:1,ftp://bad:2")


def test_proxy_with_quote_rejected():
    # Defense-in-depth against a proxy string that could break out of the
    # generated launcher source.
    with pytest.raises(ValidationError):
        _settings(proxy="http://x'); import os; os.system('x'); ('")


def test_proxy_with_whitespace_rejected():
    with pytest.raises(ValidationError):
        _settings(proxy="http://user:pass @host:8080")


def test_geoip_auto_disabled_without_proxy():
    s = Settings(geoip=True)  # no proxy
    assert s.geoip is False


def test_geoip_kept_with_proxy_pool():
    s = Settings(geoip=True, proxies="http://a:1,http://b:2")
    assert s.geoip is True


def test_to_camoufox_kwargs_per_instance_proxy():
    s = _settings(proxies="http://a:1,http://b:2")
    kw = s.to_camoufox_kwargs(proxy="http://a:1")
    assert kw["proxy"] == "http://a:1"


def test_to_camoufox_kwargs_no_proxy_disables_geoip():
    s = Settings(geoip=True, proxies="http://a:1")
    # An instance that received no proxy must not claim geoip.
    kw = s.to_camoufox_kwargs(proxy=None)
    assert kw["geoip"] is False
    assert "proxy" not in kw


def test_to_camoufox_kwargs_geoip_true_with_proxy():
    s = Settings(geoip=True, proxies="http://a:1")
    kw = s.to_camoufox_kwargs(proxy="http://a:1")
    assert kw["geoip"] is True


def test_to_camoufox_kwargs_uses_virtual_display():
    s = _settings()
    assert s.to_camoufox_kwargs()["headless"] is False


def test_priority_settings_defaults():
    s = _settings()
    assert s.max_concurrency_per_instance == 1
    assert s.preemption is False
    assert s.preempt_grace == 10.0
    assert s.restart_on_preempt is True
    assert s.acquire_timeout == 30.0
