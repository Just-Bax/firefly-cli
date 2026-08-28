from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import httpx

from . import protocol
from .errors import ApiError, NotFound, Rejected, SessionExpired, Unreachable
from .session import Session

Refresher = Callable[[Session], Session]


class Client:
    """HTTP access to Adobe's private Firefly API as one signed-in user."""

    def __init__(
        self,
        session: Session,
        timeout: float = 120.0,
        max_retries: int = 5,
        on_refresh: Refresher | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.session = session
        self.max_retries = max(1, max_retries)
        self._on_refresh = on_refresh
        self._http = httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            transport=transport,
        )

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def upload(self, data: bytes, mime: str = "image/png") -> str:
        """Store a reference image and return the blob id to attach to a job."""
        response = self.call("POST", protocol.UPLOAD_PATH, content=data, content_type=mime)
        blob = protocol.find_blob_id(_body(response))
        if not blob:
            raise ApiError(
                response.status_code,
                "Adobe accepted the reference image but returned no asset id.",
                _body(response),
                str(response.url),
            )
        return blob

    def create(self, path: str, payload: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
        """Submit a generation job. Returns (result_url, cancel_url, raw)."""
        response = self.call("POST", path, json=payload, content_type="application/json")
        body = _body(response)
        result_url, cancel_url = protocol.job_links(body)
        if not result_url:
            raise ApiError(
                response.status_code,
                "Adobe accepted the job but returned no result link.",
                body,
                str(response.url),
            )
        return result_url, cancel_url, body if isinstance(body, dict) else {}

    def poll(self, result_url: str) -> protocol.JobResult:
        response = self.call("GET", result_url)
        return protocol.read_result(_body(response))

    def cancel(self, cancel_url: str) -> None:
        self.call("POST", cancel_url)

    def fetch(self, url: str) -> bytes:
        """Download a finished asset.

        Presigned URLs carry their own credentials in the query string and
        reject a request that also sends an Authorization header, so this
        deliberately does not go through call().
        """
        with _reachable(url):
            response = self._http.get(url, headers={"user-agent": self.session.user_agent})
        if response.status_code >= 400:
            raise ApiError(
                response.status_code,
                f"Could not download the generated asset (HTTP {response.status_code}).",
                None,
                url,
            )
        return response.content

    def call(
        self,
        method: str,
        target: str,
        json: Any = None,
        content: bytes | None = None,
        content_type: str = "",
    ) -> httpx.Response:
        if self.session.needs_refresh():
            self._refresh()

        response = self._retrying(method, target, json, content, content_type)
        if response.status_code == 401:
            self._refresh()
            response = self._retrying(method, target, json, content, content_type)

        return self._checked(response)

    def _retrying(
        self,
        method: str,
        target: str,
        json: Any,
        content: bytes | None,
        content_type: str,
    ) -> httpx.Response:
        url = _absolute(target)
        response: httpx.Response | None = None

        for attempt in range(1, self.max_retries + 1):
            with _reachable(url):
                response = self._http.request(
                    method.upper(),
                    url,
                    json=json,
                    content=content,
                    headers=self.session.headers(content_type),
                )
            if response.status_code not in protocol.RETRY_STATUSES:
                return response
            if attempt < self.max_retries:
                # Adobe returns 408 for "system under load", which clears on its
                # own within a few seconds.
                time.sleep(attempt * 3)

        assert response is not None
        return response

    def _refresh(self) -> None:
        if self.session.is_static:
            raise SessionExpired(
                "the pasted credentials are no longer accepted; run 'firefly login --curl' again"
            )
        if self._on_refresh is None:
            raise SessionExpired("no way to renew the token in this context")
        self.session = self._on_refresh(self.session)

    def _checked(self, response: httpx.Response) -> httpx.Response:
        if response.status_code < 400:
            return response

        detail = _detail(response)
        url = str(response.url)
        if response.status_code == 401:
            raise SessionExpired(detail or "Adobe rejected the token")
        if response.status_code == 403:
            raise Rejected(
                detail
                or "Adobe refused the request. Usually the content policy, or no credits left."
            )
        if response.status_code == 404:
            raise NotFound(detail or f"No such Firefly endpoint: {url}. Adobe may have moved it.")
        raise ApiError(
            response.status_code, detail or f"HTTP {response.status_code}", _body(response), url
        )


def _absolute(target: str) -> str:
    """Poll and cancel links come back absolute and region-scoped; paths do not."""
    if target.startswith("http://") or target.startswith("https://"):
        return target
    return f"{protocol.HOST}/{target.lstrip('/')}"


@contextmanager
def _reachable(url: str) -> Iterator[None]:
    """httpx transport failures are not FireflyError, so unwrapped they end the
    process in a traceback instead of a message and an exit code."""
    try:
        yield
    except httpx.TimeoutException as exc:
        raise Unreachable(f"{url} timed out. Raise timeout_seconds, or check the network.") from exc
    except httpx.RequestError as exc:
        raise Unreachable(f"Could not reach {url}: {exc}") from exc


def _body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text[:2000]


def _detail(response: httpx.Response) -> str:
    payload = _body(response)
    if isinstance(payload, dict):
        for key in ("message", "error_message", "error", "detail", "reason", "description"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
    text = str(payload or "").strip()
    if not text or text.lstrip().startswith("<"):
        return ""
    return text[:1000]
