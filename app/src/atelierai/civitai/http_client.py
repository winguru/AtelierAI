from __future__ import annotations

import random
import socket
import struct
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import requests
import urllib3.util.connection as urllib3_conn


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
        rtype = struct.unpack(">H", data[idx:idx + 2])[0]
        idx += 8  # type(2) + class(2) + ttl(4)
        rdlen = struct.unpack(">H", data[idx:idx + 2])[0]
        idx += 2
        if rtype == 1 and rdlen == 4:
            answers.append(".".join(str(b) for b in data[idx:idx + 4]))
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


class _DnsFallbackAdapter(requests.adapters.HTTPAdapter):
    """HTTPAdapter that retries with manual DNS resolution on getaddrinfo failure."""

    def send(self, request, **kwargs):
        try:
            return super().send(request, **kwargs)
        except requests.ConnectionError as exc:
            # Only intercept DNS-resolution failures
            if "Failed to resolve" not in str(exc) and "nodename nor servname" not in str(exc):
                raise
            from urllib3.util import parse_url
            parsed = parse_url(request.url)
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
                return super().send(request, **kwargs)
            finally:
                urllib3_conn.create_connection = original


class CivitaiRequestError(RuntimeError):
    def __init__(self, message: str, *, status_code: Optional[int] = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class CivitaiHttpClient:
    _GLOBAL_BACKOFF_LOCK = threading.Lock()
    _GLOBAL_BACKOFF_UNTIL = 0.0
    _GLOBAL_BACKOFF_REASON = ""

    def __init__(
        self,
        headers_factory: Callable[[], dict[str, str]],
        *,
        default_timeout: tuple[float, float] = (10.0, 30.0),
        download_timeout: tuple[float, float] = (10.0, 120.0),
        max_attempts: int = 4,
        backoff_base_seconds: float = 0.75,
    ):
        self._headers_factory = headers_factory
        self._default_timeout = default_timeout
        self._download_timeout = download_timeout
        self._max_attempts = max(1, int(max_attempts))
        self._backoff_base_seconds = max(0.1, float(backoff_base_seconds))
        self._thread_local = threading.local()

    @classmethod
    def activate_global_backoff(cls, cooldown_seconds: float, *, reason: str = "rate-limit") -> float:
        cooldown = max(30.0, float(cooldown_seconds or 0.0))
        now = time.time()
        target_until = now + cooldown
        with cls._GLOBAL_BACKOFF_LOCK:
            cls._GLOBAL_BACKOFF_UNTIL = max(float(cls._GLOBAL_BACKOFF_UNTIL or 0.0), target_until)
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
        print(f"⏸️  CivitAI backoff active ({reason}); waiting {remaining:.1f}s before next request...")
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
        last_error: Optional[Exception] = None
        merged_headers = {**self._headers_factory(), **(headers or {})}
        request_timeout = timeout or self._default_timeout

        for attempt in range(1, self._max_attempts + 1):
            self._wait_for_global_backoff()
            session = self._get_session()
            try:
                response = session.request(
                    method=method,
                    url=url,
                    params=params,
                    headers=merged_headers,
                    timeout=request_timeout,
                    stream=stream,
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
                retry_after_seconds = self._retry_delay(attempt, response=response)
                enforced_wait = self.activate_global_backoff(
                    retry_after_seconds,
                    reason="HTTP 429",
                )
                print(f"⏳ CivitAI rate limit reached; enforcing global backoff for {enforced_wait:.1f}s")
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
                detail = f"CivitAI request failed with HTTP {response.status_code}"
                if body_excerpt:
                    detail = f"{detail}: {body_excerpt}"
                raise CivitaiRequestError(
                    detail,
                    status_code=response.status_code,
                    retryable=False,
                )

            return response

        if isinstance(last_error, CivitaiRequestError):
            raise last_error
        raise CivitaiRequestError("CivitAI request failed", retryable=False)

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
            raise CivitaiRequestError("CivitAI returned invalid JSON", retryable=False) from exc

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

                if expected_content_length is not None and bytes_written != expected_content_length:
                    raise CivitaiRequestError(
                        (
                            "Downloaded byte count did not match Content-Length "
                            f"({bytes_written} != {expected_content_length})"
                        ),
                        retryable=True,
                    )

                if normalized_expected_size is not None and bytes_written != normalized_expected_size:
                    if expected_content_length is not None and bytes_written == expected_content_length:
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

    def _retry_delay(self, attempt: int, response: Optional[requests.Response] = None) -> float:
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    return max(0.0, float(retry_after))
                except ValueError:
                    pass
        jitter = random.uniform(0.0, 0.35)
        return self._backoff_base_seconds * (2 ** max(0, attempt - 1)) + jitter