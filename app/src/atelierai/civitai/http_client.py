from __future__ import annotations

import os
import queue as _queue_module
import random
import socket
import struct
import tempfile
import threading
import time
from concurrent.futures import Future
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlparse

import requests
import urllib3.util.connection as urllib3_conn
from requests import PreparedRequest, Response
from requests.adapters import HTTPAdapter


# ---------------------------------------------------------------------------
# DNS fallback helpers
# ---------------------------------------------------------------------------
# On macOS the system resolver (getaddrinfo) can fail even when the network
# is healthy – see the "homenet.local" search-domain wedge bug.  We keep a
# tiny UDP-based A-record resolver so that CivitAI traffic keeps working
# when the system resolver is down.
# ---------------------------------------------------------------------------

_DNS_FALLBACK_SERVERS = ("8.8.8.8", "1.1.1.1")
_DNS_FALLBACK_TTL = 300  # seconds to cache resolved IPs


def _udp_resolve_a(hostname: str, dns_server: str, timeout: float = 3.0) -> list[str]:
    """Resolve *hostname* via a single UDP A-record query to *dns_server*."""
    txid = b"\xab\xcd"
    header = txid + b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
    question = b""
    for label in hostname.split("."):
        question += struct.pack("B", len(label)) + label.encode()
    question += b"\x00\x00\x01\x00\x01"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.sendto(header + question, (dns_server, 53))
        data, _ = s.recvfrom(4096)
    # Parse answer section
    idx = 12
    while data[idx] != 0:
        idx += data[idx] + 1
    idx += 5  # null + qtype(2) + qclass(2)
    answers: list[str] = []
    ancount = struct.unpack(">H", data[6:8])[0]
    for _ in range(ancount):
        if data[idx] & 0xC0 == 0xC0:
            idx += 2
        else:
            while data[idx] != 0:
                idx += data[idx] + 1
            idx += 1
        rtype = struct.unpack(">H", data[idx : idx + 2])[0]
        idx += 8  # type(2) + class(2) + ttl(4)
        rdlen = struct.unpack(">H", data[idx : idx + 2])[0]
        idx += 2
        if rtype == 1 and rdlen == 4:
            answers.append(".".join(str(b) for b in data[idx : idx + 4]))
        idx += rdlen
    return answers


_dns_cache: dict[str, tuple[float, list[str]]] = {}
_dns_cache_lock = threading.Lock()


def _resolve_with_fallback(hostname: str) -> list[str] | None:
    """Return cached IPs, or resolve via UDP fallback, or *None*."""
    now = time.time()
    with _dns_cache_lock:
        entry = _dns_cache.get(hostname)
        if entry and now < entry[0]:
            return entry[1]
    for ns in _DNS_FALLBACK_SERVERS:
        try:
            ips = _udp_resolve_a(hostname, ns)
            if ips:
                with _dns_cache_lock:
                    _dns_cache[hostname] = (now + _DNS_FALLBACK_TTL, ips)
                return ips
        except Exception:
            continue
    return None


class _DnsFallbackAdapter(HTTPAdapter):
    """HTTPAdapter that retries with manual DNS resolution on getaddrinfo failure."""

    def send(
        self,
        request: PreparedRequest,
        stream: bool = False,
        timeout: float | tuple[float, float] | tuple[float, None] | None = None,
        verify: bool | str = True,
        cert: bytes | str | tuple[bytes | str, bytes | str] | None = None,
        proxies: Mapping[str, str] | None = None,
    ) -> Response:
        try:
            return super().send(
                request,
                stream=stream,
                timeout=timeout,
                verify=verify,
                cert=cert,
                proxies=proxies,
            )
        except requests.ConnectionError as exc:
            # Only intercept DNS-resolution failures
            if "Failed to resolve" not in str(
                exc
            ) and "nodename nor servname" not in str(exc):
                raise
            from urllib3.util import parse_url

            request_url = request.url
            if request_url is None:
                raise
            parsed = parse_url(request_url)
            host = parsed.host
            if not host:
                raise
            ips = _resolve_with_fallback(host)
            if not ips:
                raise
            # Patch urllib3's create_connection for this one call
            original = urllib3_conn.create_connection
            resolved_host = ips[0]

            def _patched_create_connection(address, *args, **kw):
                dest_host, dest_port = address
                if dest_host == host:
                    return original((resolved_host, dest_port), *args, **kw)
                return original(address, *args, **kw)

            urllib3_conn.create_connection = _patched_create_connection
            try:
                # Set Host header so SNI / virtual-hosting works
                request.headers.setdefault("Host", host)
                return super().send(
                    request,
                    stream=stream,
                    timeout=timeout,
                    verify=verify,
                    cert=cert,
                    proxies=proxies,
                )
            finally:
                urllib3_conn.create_connection = original


# ---------------------------------------------------------------------------
# Request classification — per-type / per-FQDN / per-endpoint tracking
# ---------------------------------------------------------------------------


class RequestType(str, Enum):
    """Broad categories of CivitAI HTTP requests."""

    TRPC = "trpc"
    CDN_DOWNLOAD = "cdn_download"
    UNKNOWN = "unknown"


def _classify_request(url: str) -> tuple[RequestType, str, str]:
    """Classify *url* into ``(request_type, fqdn, endpoint)``.

    Examples::

        _classify_request("https://civitai.red/api/trpc/image.get?input=...")
        → (TRPC, "civitai.red", "image.get")

        _classify_request("https://image.civitai.com/xyz123/original=true/123")
        → (CDN_DOWNLOAD, "image.civitai.com", "/xyz123/original=true/123")

        _classify_request("https://civitai.red/api/v1/images")
        → (UNKNOWN, "civitai.red", "/api/v1/images")
    """
    parsed = urlparse(url)
    fqdn = parsed.hostname or "unknown"
    path = parsed.path or "/"

    if "/api/trpc/" in path:
        # Extract the tRPC procedure name, e.g. "image.get" from
        # "/api/trpc/image.get,input=..." or "/api/trpc/image.get"
        trpc_segment = path.split("/api/trpc/", 1)[1]
        # The procedure name ends at a comma (batch separator) or query
        # string boundary.
        endpoint = trpc_segment.split(",")[0].split("?")[0]
        return RequestType.TRPC, fqdn, endpoint

    if "cdn" in fqdn.lower() or fqdn.startswith("image."):
        # CDN path like /{uuid}/width=450/12345
        return RequestType.CDN_DOWNLOAD, fqdn, path

    return RequestType.UNKNOWN, fqdn, path


class CivitaiRequestError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


# Singleton reference — the consumer daemon thread needs an instance to call
# instance methods (session, headers factory).  We store the most recently
# created instance here.  There should only ever be one CivitaiHttpClient.
_SINGLETON_REF: list[Optional[CivitaiHttpClient]] = [None]


class CivitaiHttpClient:
    _GLOBAL_BACKOFF_LOCK = threading.Lock()
    _GLOBAL_BACKOFF_UNTIL = 0.0
    _GLOBAL_BACKOFF_REASON = ""

    # ── Request counting & proactive rate limiting ──────────────────────────
    _REQUEST_COUNTER_LOCK = threading.Lock()
    _REQUEST_TIMESTAMPS: list[float] = []  # monotonic timestamps
    _REQUEST_TOTAL = 0  # lifetime counter
    _THROTTLE_COUNT = 0  # how many times we proactively paused
    _RATE_LIMIT_RPM = 25  # target max requests per minute (conservative)
    _RATE_LIMIT_WINDOW = 60.0  # sliding window size in seconds
    _RATE_LIMIT_HEADROOM = 3  # stop this many requests before the ceiling
    _RATE_LIMITED_429 = 0  # lifetime 429 responses received

    # ── Per-type / per-FQDN / per-endpoint counters ─────────────────────────
    _TYPE_COUNTS: dict[RequestType, int] = {}  # total by type
    _TYPE_429_COUNTS: dict[RequestType, int] = {}  # 429s by type
    _FQDN_COUNTS: dict[str, int] = {}  # total by FQDN
    _ENDPOINT_COUNTS: dict[str, int] = {}  # total by endpoint name

    # ── 503 / rate-at-failure tracking ──────────────────────────────────────
    _TYPE_503_COUNTS: dict[RequestType, int] = {}  # 503s by type
    _RATE_LIMITED_503: int = 0  # lifetime 503 responses received
    _LAST_RPM_AT_429: Optional[int] = None  # observed RPM when last 429 hit
    _LAST_RPM_AT_503: Optional[int] = None  # observed RPM when last 503 hit
    _LAST_429_TIME: Optional[float] = None  # wall-clock of last 429
    _LAST_503_TIME: Optional[float] = None  # wall-clock of last 503

    # ── 403 Cloudflare tracking ─────────────────────────────────────────────
    # CivitAI rate-limits via Cloudflare challenges (HTTP 403 with
    # "Just a moment..." body).  These are the real rate-limit signal.
    _TYPE_403_CLOUDFLARE_COUNTS: dict[RequestType, int] = {}
    _RATE_LIMITED_403_CLOUDFLARE: int = 0
    _LAST_RPM_AT_403_CF: Optional[int] = None
    _LAST_403_CF_TIME: Optional[float] = None

    # ── FIFO request queue ──────────────────────────────────────────────────
    _REQUEST_QUEUE: _queue_module.Queue = _queue_module.Queue()
    _CONSUMER_THREAD: Optional[threading.Thread] = None
    _CONSUMER_LOCK = threading.Lock()
    _MIN_REQUEST_INTERVAL: float = float(
        os.environ.get("CIVITAI_MIN_REQUEST_INTERVAL", "0.25")
    )
    _QUEUE_STARTED = False

    # ── CDN download pacing ─────────────────────────────────────────────────
    # CDN (image.civitai.com) triggers 503s at high concurrency.
    # A per-download minimum interval keeps the CDN rate manageable.
    _CDN_DOWNLOAD_MIN_INTERVAL: float = float(
        os.environ.get("CIVITAI_CDN_MIN_INTERVAL", "1.0")
    )
    _LAST_CDN_DOWNLOAD_TIME: float = 0.0  # monotonic timestamp
    # ── Last-request debug info ──────────────────────────────────────────────
    # Updated by the consumer thread after each completed request.
    # Single-threaded callers (scripts) can read this immediately after a
    # blocking API call returns — the consumer has already written it by then.
    _LAST_REQUEST_INFO: Optional[dict] = None
    def __init__(
        self,
        headers_factory: Callable[[], dict[str, str]],
        *,
        default_timeout: tuple[float, float] = (10.0, 30.0),
        download_timeout: tuple[float, float] = (10.0, 120.0),
        max_attempts: int = 4,
        backoff_base_seconds: float = 0.75,
        rate_limit_rpm: int = 25,
    ):
        self._headers_factory = headers_factory
        self._default_timeout = default_timeout
        self._download_timeout = download_timeout
        self._max_attempts = max(1, int(max_attempts))
        self._backoff_base_seconds = max(0.1, float(backoff_base_seconds))
        self._thread_local = threading.local()
        # Allow per-instance override of the class-level RPM target
        if rate_limit_rpm != self._RATE_LIMIT_RPM:
            self._RATE_LIMIT_RPM = max(1, int(rate_limit_rpm))
        # Register as the singleton so the consumer daemon can call
        # instance methods.
        _SINGLETON_REF[0] = self

    # ── FIFO request queue internals ───────────────────────────────────────

    class _RequestEnvelope:
        """Internal envelope for requests placed on the FIFO queue."""

        __slots__ = (
            "method",
            "url",
            "kwargs",
            "future",
            "request_type",
            "fqdn",
            "endpoint",
            "enqueued_at",
        )

        def __init__(
            self,
            method: str,
            url: str,
            kwargs: dict[str, Any],
            future: Future,
            request_type: RequestType,
            fqdn: str,
            endpoint: str,
        ):
            self.method = method
            self.url = url
            self.kwargs = kwargs
            self.future = future
            self.request_type = request_type
            self.fqdn = fqdn
            self.endpoint = endpoint
            self.enqueued_at = time.monotonic()

    @classmethod
    def _ensure_consumer_started(cls) -> None:
        """Start the FIFO consumer daemon thread exactly once."""
        if cls._QUEUE_STARTED:
            return
        with cls._CONSUMER_LOCK:
            if cls._QUEUE_STARTED:
                return
            thread = threading.Thread(
                target=cls._request_consumer_loop,
                name="civitai-request-consumer",
                daemon=True,
            )
            cls._CONSUMER_THREAD = thread
            cls._QUEUE_STARTED = True
            thread.start()

    @classmethod
    def _increment_type_counter(
        cls, counter: dict, key: Any
    ) -> None:
        with cls._REQUEST_COUNTER_LOCK:
            counter[key] = counter.get(key, 0) + 1

    @classmethod
    def _request_consumer_loop(cls) -> None:
        """Main loop for the FIFO request consumer daemon thread.

        Dequeues one request at a time, records per-type / per-FQDN /
        per-endpoint counters, and dispatches to the singleton for execution.

        Pacing is **observational only** — requests are sent as fast as the
        queue provides them, and the observed rate is recorded so we can
        correlate it with any 429 / 503 responses.
        """
        while True:
            try:
                envelope: CivitaiHttpClient._RequestEnvelope = (
                    cls._REQUEST_QUEUE.get()
                )
            except Exception:
                # Should never happen with stdlib Queue, but guard anyway.
                time.sleep(0.5)
                continue

            try:
                # Wait out any active global backoff (reactive — only
                # triggered after an actual 429 response).
                instance = cls._get_any_instance()
                if instance is not None:
                    instance._wait_for_global_backoff()

                # Per-type / per-FQDN / per-endpoint counters
                cls._increment_type_counter(cls._TYPE_COUNTS, envelope.request_type)
                cls._increment_type_counter(cls._FQDN_COUNTS, envelope.fqdn)
                cls._increment_type_counter(cls._ENDPOINT_COUNTS, envelope.endpoint)

                # ── CDN download pacing ────────────────────────────────────
                # CDN (image.civitai.com) 503s at high concurrency.
                # Enforce a minimum interval between CDN requests.
                if envelope.request_type == RequestType.CDN_DOWNLOAD:
                    with cls._REQUEST_COUNTER_LOCK:
                        last_cdn = cls._LAST_CDN_DOWNLOAD_TIME
                        now_mono = time.monotonic()
                        wait_needed = cls._CDN_DOWNLOAD_MIN_INTERVAL - (
                            now_mono - last_cdn
                        )
                    if wait_needed > 0:
                        time.sleep(wait_needed)
                    with cls._REQUEST_COUNTER_LOCK:
                        cls._LAST_CDN_DOWNLOAD_TIME = time.monotonic()

                # Record in the sliding window (used for observed-rate metrics)
                cls._record_request()

                # Execute the actual HTTP request with retries.
                # _execute_envelope_request is an instance method (needs
                # session / headers factory), so call via the singleton.
                instance = cls._get_any_instance()
                if instance is None:
                    raise RuntimeError(
                        "CivitaiHttpClient singleton not available; "
                        "cannot execute queued request"
                    )
                response = instance._execute_envelope_request(envelope)
                envelope.future.set_result(response)
            except Exception as exc:
                if not envelope.future.done():
                    envelope.future.set_exception(exc)
            finally:
                cls._REQUEST_QUEUE.task_done()

    @classmethod
    def _get_any_instance(cls) -> Optional[CivitaiHttpClient]:
        """Return the singleton instance if one exists.

        Used by the consumer thread to call instance methods like
        ``_wait_for_global_backoff()``.
        """
        return _SINGLETON_REF[0] if _SINGLETON_REF else None

    def _execute_envelope_request(
        self, envelope: _RequestEnvelope
    ) -> requests.Response:
        """Send the HTTP request described by *envelope*, with full retry logic.

        This is the core of the consumer thread — it handles retries, 429
        backoff, and server-error retries identically to the old inline
        ``request()`` method.
        """
        merged_headers = {
            **self._headers_factory(),
            **(envelope.kwargs.get("headers") or {}),
        }
        request_timeout = envelope.kwargs.get("timeout") or self._default_timeout
        last_error: Optional[Exception] = None

        for attempt in range(1, self._max_attempts + 1):
            session = self._get_session()
            try:
                response = session.request(
                    method=envelope.method,
                    url=envelope.url,
                    params=envelope.kwargs.get("params"),
                    headers=merged_headers,
                    timeout=request_timeout,
                    stream=envelope.kwargs.get("stream", False),
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                if attempt >= self._max_attempts:
                    raise CivitaiRequestError(
                        f"Request failed after {attempt} attempts: {exc}",
                        retryable=True,
                    ) from exc
                time.sleep(self._retry_delay(attempt))
                continue
            except requests.RequestException as exc:
                raise CivitaiRequestError(str(exc), retryable=False) from exc

            if response.status_code == 429:
                # Snapshot the observed request rate at time of 429
                rpm_now = self.__class__._current_rpm()
                self._increment_type_counter(
                    self.__class__._TYPE_429_COUNTS, envelope.request_type
                )
                with self.__class__._REQUEST_COUNTER_LOCK:
                    self.__class__._RATE_LIMITED_429 += 1
                    self.__class__._LAST_RPM_AT_429 = rpm_now
                    self.__class__._LAST_429_TIME = time.time()
                retry_after_seconds = self._retry_delay(attempt, response=response)
                enforced_wait = self.activate_global_backoff(
                    retry_after_seconds, reason="HTTP 429"
                )
                print(
                    f"⏳ CivitAI rate limit reached ({envelope.request_type.value} "
                    f"{envelope.endpoint}) at {rpm_now} RPM; "
                    f"enforcing global backoff for {enforced_wait:.1f}s"
                )
                last_error = CivitaiRequestError(
                    "CivitAI rate limit reached (HTTP 429)",
                    status_code=429,
                    retryable=True,
                )
                if attempt >= self._max_attempts:
                    raise last_error
                continue

            if 500 <= response.status_code < 600:
                last_error = CivitaiRequestError(
                    f"CivitAI server error (HTTP {response.status_code})",
                    status_code=response.status_code,
                    retryable=True,
                )
                # Track 503 separately — it usually signals rate-limiting
                # at the CDN / reverse-proxy layer rather than app-level 429.
                if response.status_code == 503:
                    rpm_now = self.__class__._current_rpm()
                    self._increment_type_counter(
                        self.__class__._TYPE_503_COUNTS, envelope.request_type
                    )
                    with self.__class__._REQUEST_COUNTER_LOCK:
                        self.__class__._RATE_LIMITED_503 += 1
                        self.__class__._LAST_RPM_AT_503 = rpm_now
                        self.__class__._LAST_503_TIME = time.time()
                    print(
                        f"🚫 CivitAI 503 ({envelope.request_type.value} "
                        f"{envelope.endpoint}) at {rpm_now} RPM"
                    )
                if attempt >= self._max_attempts:
                    raise last_error
                time.sleep(self._retry_delay(attempt, response=response))
                continue

            if response.status_code >= 400:
                body_excerpt = ""
                try:
                    body_excerpt = response.text[:240].strip()
                except Exception:
                    body_excerpt = ""

                # ── Detect Cloudflare challenge (403) ────────────────────────
                # CivitAI rate-limits via Cloudflare "Just a moment..." pages.
                # Track these as rate-limit events so they appear in metrics.
                if response.status_code == 403 and "Just a moment" in body_excerpt:
                    rpm_now = self.__class__._current_rpm()
                    self._increment_type_counter(
                        self.__class__._TYPE_403_CLOUDFLARE_COUNTS,
                        envelope.request_type,
                    )
                    with self.__class__._REQUEST_COUNTER_LOCK:
                        self.__class__._RATE_LIMITED_403_CLOUDFLARE += 1
                        self.__class__._LAST_RPM_AT_403_CF = rpm_now
                        self.__class__._LAST_403_CF_TIME = time.time()
                    print(
                        f"🚫 CivitAI 403 Cloudflare "
                        f"({envelope.request_type.value} {envelope.endpoint}) "
                        f"at {rpm_now} RPM"
                    )

                detail = f"CivitAI request failed with HTTP {response.status_code}"
                if body_excerpt:
                    detail = f"{detail}: {body_excerpt}"
                raise CivitaiRequestError(
                    detail,
                    status_code=response.status_code,
                    retryable=False,
                )

            # Record metadata for debug consumers (e.g. verbose script flags).
            try:
                base_url = (response.url or envelope.url).split("?")[0]
                self.__class__._LAST_REQUEST_INFO = {
                    "url": base_url,
                    "status_code": response.status_code,
                    "content_length": len(response.content),
                    "elapsed_seconds": (
                        response.elapsed.total_seconds()
                        if response.elapsed is not None
                        else None
                    ),
                    "endpoint": envelope.endpoint,
                }
            except Exception:
                pass

            return response

        if isinstance(last_error, CivitaiRequestError):
            raise last_error
        raise CivitaiRequestError("CivitAI request failed", retryable=False)

    # ── Request metrics ────────────────────────────────────────────────────

    @classmethod
    def get_last_request_info(cls) -> Optional[dict]:
        """Return metadata for the most recently completed HTTP request.

        Keys: url (base, no query string), status_code, content_length (bytes),
        elapsed_seconds (HTTP round-trip), endpoint (tRPC/REST name).
        Returns None if no request has completed yet.
        """
        return cls._LAST_REQUEST_INFO

    @classmethod
    def get_request_metrics(cls) -> dict[str, Any]:
        """Return a snapshot of request-counting metrics.

        Keys:
            rpm_window: requests in the last 60 s sliding window
            observed_rps: observed requests-per-second (rpm_window / window)
            rpm_limit: configured RPM ceiling (informational — not enforced)
            total_requests: lifetime request count
            throttle_count: always 0 (pacing removed)
            rate_limited_429: number of HTTP 429 responses received
            rate_limited_503: number of HTTP 503 responses received
            rate_limited_403_cloudflare: number of Cloudflare 403 challenges received
            last_rpm_at_429: RPM in sliding window when last 429 hit (or None)
            last_rpm_at_503: RPM in sliding window when last 503 hit (or None)
            last_rpm_at_403_cf: RPM when last Cloudflare 403 hit (or None)
            last_429_time: wall-clock time of last 429 (or None)
            last_503_time: wall-clock time of last 503 (or None)
            last_403_cf_time: wall-clock time of last Cloudflare 403 (or None)
            backoff_active: whether a global backoff is currently active
            backoff_remaining_seconds: seconds remaining in current backoff
            queue_depth: number of requests waiting in the FIFO queue
            min_interval: configured minimum interval (seconds — not enforced)
            consumer_alive: whether the FIFO consumer thread is running
            type_counts: lifetime requests broken down by RequestType
            type_429_counts: 429 responses broken down by RequestType
            type_503_counts: 503 responses broken down by RequestType
            type_403_cloudflare_counts: Cloudflare 403 challenges by RequestType
            fqdn_counts: lifetime requests broken down by FQDN
            endpoint_counts: lifetime requests broken down by endpoint name
        """
        now = time.time()
        with cls._REQUEST_COUNTER_LOCK:
            window_start = now - cls._RATE_LIMIT_WINDOW
            rpm_window = sum(
                1 for ts in cls._REQUEST_TIMESTAMPS if ts >= window_start
            )
            total = cls._REQUEST_TOTAL
            throttles = cls._THROTTLE_COUNT
            limited = cls._RATE_LIMITED_429
            rpm_limit = cls._RATE_LIMIT_RPM
            type_counts = dict(cls._TYPE_COUNTS)
            type_429_counts = dict(cls._TYPE_429_COUNTS)
            fqdn_counts = dict(cls._FQDN_COUNTS)
            endpoint_counts = dict(cls._ENDPOINT_COUNTS)

        consumer_thread = cls._CONSUMER_THREAD
        consumer_alive = (
            consumer_thread is not None and consumer_thread.is_alive()
        )

        with cls._REQUEST_COUNTER_LOCK:
            rate_limited_503 = cls._RATE_LIMITED_503
            last_rpm_429 = cls._LAST_RPM_AT_429
            last_rpm_503 = cls._LAST_RPM_AT_503
            last_429_time = cls._LAST_429_TIME
            last_503_time = cls._LAST_503_TIME
            type_503_counts = dict(cls._TYPE_503_COUNTS)
            rate_limited_403_cf = cls._RATE_LIMITED_403_CLOUDFLARE
            last_rpm_403_cf = cls._LAST_RPM_AT_403_CF
            last_403_cf_time = cls._LAST_403_CF_TIME
            type_403_cf_counts = dict(cls._TYPE_403_CLOUDFLARE_COUNTS)
            cdn_min_interval = cls._CDN_DOWNLOAD_MIN_INTERVAL

        # Observed requests-per-second (across the sliding window)
        observed_rps = round(rpm_window / cls._RATE_LIMIT_WINDOW, 2) if rpm_window else 0.0

        return {
            "rpm_window": rpm_window,
            "observed_rps": observed_rps,
            "rpm_limit": rpm_limit,
            "total_requests": total,
            "throttle_count": throttles,
            "rate_limited_429": limited,
            "rate_limited_503": rate_limited_503,
            "rate_limited_403_cloudflare": rate_limited_403_cf,
            "last_rpm_at_429": last_rpm_429,
            "last_rpm_at_503": last_rpm_503,
            "last_rpm_at_403_cf": last_rpm_403_cf,
            "last_429_time": last_429_time,
            "last_503_time": last_503_time,
            "last_403_cf_time": last_403_cf_time,
            "backoff_active": cls.is_global_backoff_active(),
            "backoff_remaining_seconds": round(
                cls.get_global_backoff_remaining_seconds(), 1
            ),
            "queue_depth": cls._REQUEST_QUEUE.qsize(),
            "min_interval": cls._MIN_REQUEST_INTERVAL,
            "consumer_alive": consumer_alive,
            "type_counts": {
                rt.value: type_counts.get(rt, 0) for rt in RequestType
            },
            "type_429_counts": {
                rt.value: type_429_counts.get(rt, 0) for rt in RequestType
            },
            "type_503_counts": {
                rt.value: type_503_counts.get(rt, 0) for rt in RequestType
            },
            "type_403_cloudflare_counts": {
                rt.value: type_403_cf_counts.get(rt, 0) for rt in RequestType
            },
            "cdn_min_interval": cdn_min_interval,
            "fqdn_counts": fqdn_counts,
            "endpoint_counts": endpoint_counts,
        }

    @classmethod
    def _record_request(cls) -> None:
        """Record an outgoing request in the sliding window."""
        now = time.time()
        with cls._REQUEST_COUNTER_LOCK:
            cls._REQUEST_TIMESTAMPS.append(now)
            cls._REQUEST_TOTAL += 1
            # Prune timestamps older than the window to keep the list bounded
            window_start = now - cls._RATE_LIMIT_WINDOW
            cls._REQUEST_TIMESTAMPS = [
                ts for ts in cls._REQUEST_TIMESTAMPS if ts >= window_start
            ]

    @classmethod
    def _current_rpm(cls) -> int:
        """Return the number of requests in the current sliding window."""
        now = time.time()
        with cls._REQUEST_COUNTER_LOCK:
            window_start = now - cls._RATE_LIMIT_WINDOW
            return sum(1 for ts in cls._REQUEST_TIMESTAMPS if ts >= window_start)

    @classmethod
    def activate_global_backoff(
        cls, cooldown_seconds: float, *, reason: str = "rate-limit"
    ) -> float:
        cooldown = max(30.0, float(cooldown_seconds or 0.0))
        now = time.time()
        target_until = now + cooldown
        with cls._GLOBAL_BACKOFF_LOCK:
            cls._GLOBAL_BACKOFF_UNTIL = max(
                float(cls._GLOBAL_BACKOFF_UNTIL or 0.0), target_until
            )
            if reason:
                cls._GLOBAL_BACKOFF_REASON = str(reason)
            return max(0.0, cls._GLOBAL_BACKOFF_UNTIL - now)

    @classmethod
    def get_global_backoff_remaining_seconds(cls) -> float:
        with cls._GLOBAL_BACKOFF_LOCK:
            return max(0.0, float(cls._GLOBAL_BACKOFF_UNTIL or 0.0) - time.time())

    @classmethod
    def is_global_backoff_active(cls) -> bool:
        return cls.get_global_backoff_remaining_seconds() > 0.0

    def _wait_for_global_backoff(self) -> None:
        remaining = self.get_global_backoff_remaining_seconds()
        if remaining <= 0.0:
            return
        reason = ""
        with self._GLOBAL_BACKOFF_LOCK:
            reason = str(self._GLOBAL_BACKOFF_REASON or "rate-limit")
        print(
            f"⏸️  CivitAI backoff active ({reason}); waiting {remaining:.1f}s before next request..."
        )
        while remaining > 0.0:
            time.sleep(min(1.0, remaining))
            remaining = self.get_global_backoff_remaining_seconds()

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        timeout: Optional[tuple[float, float]] = None,
        stream: bool = False,
    ) -> requests.Response:
        """Enqueue an HTTP request through the FIFO queue and block until done.

        The actual HTTP send happens on the consumer daemon thread, which
        enforces minimum interval pacing and sliding-window ceiling checks.
        Retries are handled entirely within the consumer thread.
        """
        # Early exit: if global backoff is very long, fail fast so callers
        # don't sit in the queue for ages.
        self._wait_for_global_backoff()

        # Classify the request for per-type / per-FQDN / per-endpoint tracking
        request_type, fqdn, endpoint = _classify_request(url)

        # Build the envelope
        future: Future[requests.Response] = Future()
        envelope = self._RequestEnvelope(
            method=method,
            url=url,
            kwargs={
                "params": params,
                "headers": headers,
                "timeout": timeout,
                "stream": stream,
            },
            future=future,
            request_type=request_type,
            fqdn=fqdn,
            endpoint=endpoint,
        )

        # Ensure the consumer daemon is running, then enqueue
        self._ensure_consumer_started()
        self._REQUEST_QUEUE.put(envelope)

        # Block until the consumer fills the future (or raises)
        return future.result()

    def request_json(
        self,
        method: str,
        url: str,
        *,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        timeout: Optional[tuple[float, float]] = None,
    ) -> Any:
        response = self.request(
            method,
            url,
            params=params,
            headers=headers,
            timeout=timeout,
            stream=False,
        )
        try:
            return response.json()
        except ValueError as exc:
            raise CivitaiRequestError(
                "CivitAI returned invalid JSON", retryable=False
            ) from exc

    def download_to_temp(
        self,
        url: str,
        *,
        output_dir: str | Path,
        prefix: str,
        suffix: str,
        headers: Optional[dict[str, str]] = None,
        timeout: Optional[tuple[float, float]] = None,
        chunk_size: int = 1024 * 1024,
        expected_size_bytes: Optional[int] = None,
    ) -> Path:
        output_root = Path(output_dir)
        output_root.mkdir(parents=True, exist_ok=True)

        normalized_expected_size: Optional[int] = None
        if expected_size_bytes is not None:
            try:
                parsed_expected = int(expected_size_bytes)
            except (TypeError, ValueError):
                parsed_expected = 0
            if parsed_expected > 0:
                normalized_expected_size = parsed_expected

        for attempt in range(1, self._max_attempts + 1):
            response: Optional[requests.Response] = None
            temp_path: Optional[Path] = None
            try:
                response = self.request(
                    "GET",
                    url,
                    headers=headers,
                    timeout=timeout or self._download_timeout,
                    stream=True,
                )

                content_length_header = response.headers.get("Content-Length")
                expected_content_length: Optional[int] = None
                if content_length_header:
                    try:
                        parsed_content_length = int(content_length_header)
                    except ValueError:
                        parsed_content_length = 0
                    if parsed_content_length > 0:
                        expected_content_length = parsed_content_length

                bytes_written = 0
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix=prefix,
                    suffix=suffix,
                    dir=str(output_root),
                    delete=False,
                ) as temp_file:
                    temp_path = Path(temp_file.name)
                    for chunk in response.iter_content(chunk_size=chunk_size):
                        if chunk:
                            temp_file.write(chunk)
                            bytes_written += len(chunk)

                if (
                    expected_content_length is not None
                    and bytes_written != expected_content_length
                ):
                    raise CivitaiRequestError(
                        (
                            "Downloaded byte count did not match Content-Length "
                            f"({bytes_written} != {expected_content_length})"
                        ),
                        retryable=True,
                    )

                if (
                    normalized_expected_size is not None
                    and bytes_written != normalized_expected_size
                ):
                    if (
                        expected_content_length is not None
                        and bytes_written == expected_content_length
                    ):
                        # Some CivitAI metadata payloads report stale/variant sizes.
                        # If transport-level length matches the full response, trust the
                        # completed download and allow ingest to continue.
                        print(
                            "⚠️  Declared CivitAI size differed from downloaded bytes "
                            f"({bytes_written} != {normalized_expected_size}); "
                            "using completed response size based on Content-Length."
                        )
                    else:
                        raise CivitaiRequestError(
                            (
                                "Downloaded byte count did not match declared size "
                                f"({bytes_written} != {normalized_expected_size})"
                            ),
                            retryable=True,
                        )

                return temp_path
            except CivitaiRequestError as exc:
                if temp_path is not None and temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass

                if attempt >= self._max_attempts or not exc.retryable:
                    raise

                time.sleep(self._retry_delay(attempt, response=response))
            finally:
                if response is not None:
                    response.close()

        raise CivitaiRequestError("CivitAI download failed", retryable=False)

    def _get_session(self) -> requests.Session:
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = requests.Session()
            # Mount a DNS-aware adapter that falls back to direct UDP resolution
            # when the system resolver (getaddrinfo) fails.
            session.mount("https://", _DnsFallbackAdapter())
            self._thread_local.session = session
        return session

    def _retry_delay(
        self, attempt: int, response: Optional[requests.Response] = None
    ) -> float:
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    return max(0.0, float(retry_after))
                except ValueError:
                    pass
        jitter = random.uniform(0.0, 0.35)
        return self._backoff_base_seconds * (2 ** max(0, attempt - 1)) + jitter
