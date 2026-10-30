import httpx
import pytest

from ps5_monitor.http import Blocked, FetchError, Fetcher, host_allowed
from tests.conftest import PS_URL

HOSTS = ("direct.playstation.com",)


def fetcher(handler, sleeps=None, **kw):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Fetcher(client, sleep=(sleeps.append if sleeps is not None else lambda s: None), respect_robots=kw.pop("respect_robots", False), **kw)


def test_retries_transient_errors_with_exponential_backoff():
    calls, sleeps = [], []

    def handler(req):
        calls.append(1)
        return httpx.Response(503) if len(calls) < 3 else httpx.Response(200, text="ok")

    assert fetcher(handler, sleeps).get(PS_URL, HOSTS) == "ok"
    assert len(calls) == 3 and sleeps == [1.0, 2.0]


def test_retries_are_bounded():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(500)

    with pytest.raises(FetchError):
        fetcher(handler).get(PS_URL, HOSTS)
    assert len(calls) == 3


def test_network_errors_are_bounded_and_wrapped():
    def handler(req):
        raise httpx.ConnectTimeout("boom")

    with pytest.raises(FetchError, match="network error"):
        fetcher(handler).get(PS_URL, HOSTS)


@pytest.mark.parametrize("status", [401, 403, 451])
def test_access_refused_is_blocked_without_retry(status):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(status)

    with pytest.raises(Blocked):
        fetcher(handler).get(PS_URL, HOSTS)
    assert len(calls) == 1


def test_persistent_429_is_blocked():
    with pytest.raises(Blocked):
        fetcher(lambda r: httpx.Response(429, headers={"retry-after": "1"})).get(PS_URL, HOSTS)


def test_redirect_to_other_host_is_refused():
    def handler(req):
        return httpx.Response(302, headers={"location": "https://evil.example.com/x"})

    with pytest.raises(Blocked, match=r"non-allowed URL: evil\.example\.com/x"):
        fetcher(handler).get(PS_URL, HOSTS)


def test_redirect_within_allowed_host_is_followed():
    def handler(req):
        if req.url.path.endswith("start"):
            return httpx.Response(301, headers={"location": "/de-de/final"})
        return httpx.Response(200, text="final")

    assert fetcher(handler).get("https://direct.playstation.com/de-de/start", HOSTS) == "final"


def test_redirect_loop_is_bounded():
    with pytest.raises(FetchError, match="too many redirects"):
        fetcher(lambda r: httpx.Response(302, headers={"location": PS_URL})).get(PS_URL, HOSTS)


@pytest.mark.parametrize("url,ok", [
    ("https://direct.playstation.com/x", True),
    ("http://direct.playstation.com/x", False),
    ("https://direct.playstation.com.evil.com/x", False),
    ("https://user:pw@direct.playstation.com/x", False),
    ("https://direct.playstation.com:8443/x", False),
])
def test_host_validation(url, ok):
    assert host_allowed(url, HOSTS) is ok


def test_robots_txt_disallow_is_respected():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /de-de/")
        pytest.fail("page must not be requested")

    with pytest.raises(Blocked, match="robots"):
        fetcher(handler, respect_robots=True).get(PS_URL, HOSTS)


def test_robots_txt_allow_then_fetch():
    def handler(req):
        return httpx.Response(200, text="User-agent: *\nDisallow: /private/" if req.url.path == "/robots.txt" else "page")

    assert fetcher(handler, respect_robots=True).get(PS_URL, HOSTS) == "page"
