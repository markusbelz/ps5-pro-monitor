"""Polite, bounded HTTP fetching.

* identifies itself honestly (no browser impersonation), no proxies/identity rotation
* validates every URL (https, allowed host) *before* requesting it, including each redirect hop
* honours robots.txt
* bounded retries with exponential backoff for transient errors only
* 401/403/451 or persistent 429 => Blocked: we stop; we never try to get around protections
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "ps5-pro-monitor/0.1 (personal availability notifier; low frequency)"
ROBOTS_TOKEN = "ps5-pro-monitor"
MAX_REDIRECTS = 5
MAX_BODY_BYTES = 3_000_000


class FetchError(Exception):
    """Network/HTTP failure after bounded retries."""


class Blocked(Exception):
    """Access refused (robots.txt, 403, bot protection, rate limit). Never worked around."""


def host_allowed(url: str, allowed_hosts: tuple[str, ...]) -> bool:
    parts = urlsplit(url)
    host = parts.hostname or ""
    try:
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and not parts.username
        and port in (None, 443)
        and any(host == h or host.endswith("." + h) for h in allowed_hosts)
    )


def make_client(timeout: float) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        follow_redirects=False,  # redirects are followed manually so each hop is validated
        headers={"User-Agent": USER_AGENT, "Accept-Language": "de-DE,de;q=0.9", "Accept": "text/html"},
    )


@dataclass(frozen=True)
class Page:
    final_url: str
    text: str


class Fetcher:
    def __init__(
        self,
        client: httpx.Client,
        *,
        max_attempts: int = 3,
        backoff_base: float = 1.0,
        respect_robots: bool = True,
        sleep: Callable[[float], None] = time.sleep,
        host_delay: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._host_delay = host_delay
        self._clock = clock
        self._last_request: dict[str, float] = {}
        self._client = client
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base
        self._respect_robots = respect_robots
        self._sleep = sleep
        self._robots: dict[str, RobotFileParser | None] = {}

    def get(self, url: str, allowed_hosts: tuple[str, ...]) -> str:
        return self.fetch(url, allowed_hosts).text

    def fetch(self, url: str, allowed_hosts: tuple[str, ...]) -> Page:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            if not host_allowed(current, allowed_hosts):
                target = urlsplit(current)
                raise Blocked(f"refusing to request non-allowed URL: {target.hostname}{target.path[:80]}")
            if self._respect_robots and not self._robots_allows(current):
                raise Blocked("robots.txt disallows this URL")
            response = self._request(current)
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise FetchError("redirect without Location header")
                current = urljoin(current, location)
                continue
            if response.status_code in (401, 403, 451):
                raise Blocked(f"HTTP {response.status_code} (access refused)")
            if response.status_code == 429:
                raise Blocked("HTTP 429 (rate limited) after retries")
            if response.status_code >= 400:
                raise FetchError(f"HTTP {response.status_code}")
            if len(response.content) > MAX_BODY_BYTES:
                raise FetchError("response too large")
            return Page(final_url=current, text=response.text)
        raise FetchError("too many redirects")

    def _pace(self, url: str) -> None:
        """Keep at least host_delay seconds between two requests to the same host."""
        if self._host_delay <= 0:
            return
        host = urlsplit(url).netloc
        wait = self._last_request.get(host, -1e9) + self._host_delay - self._clock()
        if wait > 0:
            self._sleep(wait)
        self._last_request[host] = self._clock()

    def _request(self, url: str) -> httpx.Response:
        last = "unknown error"
        for attempt in range(1, self._max_attempts + 1):
            self._pace(url)
            delay = min(self._backoff_base * 2 ** (attempt - 1), 30.0)
            try:
                response = self._client.get(url)
            except httpx.HTTPError as exc:
                last = f"network error ({type(exc).__name__})"
            else:
                if response.status_code == 429 or response.status_code >= 500:
                    last = f"HTTP {response.status_code}"
                    retry_after = response.headers.get("retry-after", "")
                    if retry_after.isdigit():
                        delay = min(float(retry_after), 30.0)
                    if attempt == self._max_attempts:
                        return response  # caller maps 429 -> Blocked, 5xx -> FetchError
                else:
                    return response
            if attempt < self._max_attempts:
                log.warning("transient fetch failure, backing off", extra={"attempt": attempt, "reason": last})
                self._sleep(delay)
        raise FetchError(f"{last} after {self._max_attempts} attempts")

    def _robots_allows(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser: RobotFileParser | None = None
            try:
                response = self._client.get(f"{origin}/robots.txt")
                if response.status_code == 200:
                    parser = RobotFileParser()
                    parser.parse(response.text.splitlines())
            except httpx.HTTPError:
                parser = None  # robots.txt unreachable: proceed (standard behaviour)
            self._robots[origin] = parser
        parser = self._robots[origin]
        return True if parser is None else parser.can_fetch(ROBOTS_TOKEN, url)
