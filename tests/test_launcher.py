"""The generated launcher script starts Camoufox through its public API."""

from __future__ import annotations

from conftest import make_pool


def test_launcher_repr_quotes_proxy_and_stays_valid_python():
    pool = make_pool(n=1)
    # Test the generator directly so a proxy string cannot become Python code.
    payload = "http://x'); import os; os.system('PWNED'); ('"
    script = pool._generate_launcher_script(9222, payload)

    # 1) The generated source must be syntactically valid: the payload did NOT
    #    break out of its string literal.
    compile(script, "<launcher>", "exec")

    # 2) The payload is carried as a repr()-escaped string literal, not code.
    assert repr(payload) in script
    # The dangerous sequence never appears as bare, executable source.
    assert "; import os; os.system('PWNED')" not in script.replace(repr(payload), "")


def test_launcher_uses_virtual_display_with_public_server_api():
    pool = make_pool(n=1)
    script = pool._generate_launcher_script(9222, "http://user:pass@host:8080")
    compile(script, "<launcher>", "exec")

    assert "from camoufox.server import launch_server" in script
    assert "port=9222" in script
    assert "proxy='http://user:pass@host:8080'" in script
    assert "from camoufox.virtdisplay import VirtualDisplay" in script
    assert "virtual_display = VirtualDisplay" in script
    assert "headless=False" in script
    assert "virtual_display=virtual_display.get()" in script
    assert "virtual_display.kill()" in script
