"""External links open in Opera, never the Windows default browser.

The endpoint starts a program, so the host check is the security boundary:
only https, and only a host on the list. These tests pin that down, including
the lookalike domains a wildcard rule gets wrong when written carelessly.
"""

from app.config import Settings
from app.routers.meta import _host_allowed

HOSTS = Settings().open_url_hosts


def test_exact_hosts_are_allowed():
    assert _host_allowed("claude.ai", HOSTS)
    assert _host_allowed("example.com", HOSTS)


def test_subdomains_match_a_wildcard():
    assert _host_allowed("mail.google.com", HOSTS)
    assert _host_allowed("sub.claude.ai", HOSTS)


def test_case_does_not_matter():
    assert _host_allowed("Claude.AI", HOSTS)


def test_unknown_hosts_are_refused():
    assert not _host_allowed("evil.test", HOSTS)
    assert not _host_allowed(None, HOSTS)


def test_a_lookalike_domain_is_refused():
    """"*.google.com" must not match "notgoogle.com", and a trusted name in
    front of someone else's domain must not pass either."""
    assert not _host_allowed("notgoogle.com", HOSTS)
    assert not _host_allowed("fakegoogle.com", HOSTS)
    assert not _host_allowed("claude.ai.evil.test", HOSTS)


def test_only_https_is_opened(client):
    response = client.post("/api/open-in-opera", json={"url": "http://claude.ai/x"})
    assert response.status_code == 400


def test_a_disallowed_host_is_refused(client):
    response = client.post("/api/open-in-opera", json={"url": "https://evil.test/x"})
    assert response.status_code == 400
