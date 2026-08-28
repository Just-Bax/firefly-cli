from __future__ import annotations

import base64
import binascii
import json
import os
import re
import shlex
import time
from dataclasses import dataclass, field
from typing import Any

from . import protocol, store
from .errors import NotLoggedIn

BEARER_ENV = "FIREFLY_BEARER"
API_KEY_ENV = "FIREFLY_API_KEY"
ACCOUNT_ENV = "FIREFLY_ACCOUNT_ID"
ARP_ENV = "FIREFLY_ARP_SESSION_ID"
NONCE_ENV = "FIREFLY_NONCE"

# Re-harvest this far ahead of the recorded expiry rather than waiting for a
# 401, so a long poll loop does not die halfway through a generation.
REFRESH_MARGIN_SECONDS = 600

# An IMS token with no readable expiry is assumed short-lived; a 401 still
# triggers a re-harvest, which is what actually keeps this correct.
ASSUMED_TTL_SECONDS = 3600

MODE_BROWSER = "browser"
MODE_MANUAL = "manual"

_CURL_HEADER = re.compile(r"^\s*([A-Za-z0-9-]+)\s*:\s*(.*)$", re.DOTALL)


@dataclass
class Session:
    """The header set that makes a request look like the Firefly web app.

    Three of these are session-scoped rather than per-request (the IMS bearer,
    the ARP session id and the nonce), which is the only reason replaying them
    from outside a browser works at all.
    """

    bearer: str = ""
    api_key: str = protocol.DEFAULT_API_KEY
    ims_client_id: str = protocol.DEFAULT_API_KEY
    account_id: str = ""
    arp_session_id: str = ""
    nonce: str = ""
    user_agent: str = protocol.DEFAULT_USER_AGENT
    mode: str = MODE_BROWSER
    expires_at: float = 0.0
    saved_at: float = field(default_factory=time.time)

    @property
    def is_static(self) -> bool:
        """Pasted credentials have no browser profile behind them, so nothing
        can be renewed without the user."""
        return self.mode == MODE_MANUAL

    def seconds_left(self) -> float | None:
        if not self.expires_at:
            return None
        return self.expires_at - time.time()

    def needs_refresh(self) -> bool:
        if self.is_static:
            return False
        if not self.bearer:
            return True
        left = self.seconds_left()
        return left is not None and left < REFRESH_MARGIN_SECONDS

    def headers(self, content_type: str = "") -> dict[str, str]:
        headers = {
            "accept": "*/*",
            protocol.AUTH_HEADER: f"Bearer {self.bearer}",
            "origin": protocol.WEB_ORIGIN,
            "referer": f"{protocol.WEB_ORIGIN}/",
            "user-agent": self.user_agent,
            protocol.API_KEY_HEADER: self.api_key,
            protocol.IMS_CLIENT_HEADER: self.ims_client_id,
        }
        # Adobe rejects an empty value for these where it tolerates the header
        # being absent, so only send what was actually captured.
        for name, value in (
            (protocol.ACCOUNT_HEADER, self.account_id),
            (protocol.ARP_HEADER, self.arp_session_id),
            (protocol.NONCE_HEADER, self.nonce),
        ):
            if value:
                headers[name] = value
        if content_type:
            headers["content-type"] = content_type
        return headers

    def to_dict(self) -> dict[str, Any]:
        return {
            "bearer": self.bearer,
            "api_key": self.api_key,
            "ims_client_id": self.ims_client_id,
            "account_id": self.account_id,
            "arp_session_id": self.arp_session_id,
            "nonce": self.nonce,
            "user_agent": self.user_agent,
            "mode": self.mode,
            "expires_at": self.expires_at,
            "saved_at": self.saved_at,
        }

    def redacted(self) -> dict[str, Any]:
        left = self.seconds_left()
        return {
            "account_id": self.account_id or None,
            "api_key": self.api_key,
            "mode": self.mode,
            "bearer": _tail(self.bearer),
            "arp_session_id": _tail(self.arp_session_id),
            "nonce": _tail(self.nonce),
            "expires_at": _stamp(self.expires_at),
            "seconds_left": round(left) if left is not None else None,
            "expired": left is not None and left <= 0,
        }


def _tail(value: str) -> str | None:
    if not value:
        return None
    return f"...{value[-8:]}" if len(value) > 8 else "set"


def _stamp(epoch: float) -> str | None:
    if not epoch:
        return None
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))


def from_headers(headers: dict[str, str], mode: str = MODE_BROWSER) -> Session:
    """Build a session from one captured request's headers."""
    lowered = {k.lower(): v for k, v in headers.items() if v}
    bearer = lowered.get(protocol.AUTH_HEADER, "")
    if bearer.lower().startswith("bearer "):
        bearer = bearer[7:].strip()

    return Session(
        bearer=bearer,
        api_key=lowered.get(protocol.API_KEY_HEADER) or protocol.DEFAULT_API_KEY,
        ims_client_id=(
            lowered.get(protocol.IMS_CLIENT_HEADER)
            or lowered.get(protocol.API_KEY_HEADER)
            or protocol.DEFAULT_API_KEY
        ),
        account_id=lowered.get(protocol.ACCOUNT_HEADER, ""),
        arp_session_id=lowered.get(protocol.ARP_HEADER, ""),
        nonce=lowered.get(protocol.NONCE_HEADER, ""),
        user_agent=lowered.get("user-agent") or protocol.DEFAULT_USER_AGENT,
        mode=mode,
        expires_at=token_expiry(bearer),
    )


def headers_from_curl(text: str) -> dict[str, str]:
    """Pull the headers out of a DevTools 'Copy as cURL' block.

    Pasting one blob is far less error prone than transcribing six values by
    hand, and both cmd and bash flavours of the copy differ only in quoting,
    which shlex already handles.
    """
    cleaned = text.replace("^\n", " ").replace("\\\n", " ").replace("`\n", " ")
    try:
        tokens = shlex.split(cleaned)
    except ValueError as exc:
        raise ValueError(f"Could not read that as a curl command: {exc}") from None

    headers: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        value = ""
        if token in ("-H", "--header") and index + 1 < len(tokens):
            value = tokens[index + 1]
            index += 2
        elif token.startswith("-H") and len(token) > 2:
            value = token[2:]
            index += 1
        else:
            index += 1
            continue

        match = _CURL_HEADER.match(value)
        if match:
            headers[match.group(1).lower()] = match.group(2).strip()

    if not headers:
        raise ValueError("No -H headers found in that curl command.")
    return headers


def token_expiry(bearer: str) -> float:
    """When an Adobe IMS access token stops working.

    IMS puts created_at and expires_in in the payload as millisecond strings and
    omits the standard exp claim, so both spellings are read. A signature is
    never checked here: this is a local scheduling hint, not a trust decision.
    """
    payload = _jwt_payload(bearer)
    if payload is None:
        return time.time() + ASSUMED_TTL_SECONDS if bearer else 0.0

    created = _number(payload.get("created_at"))
    lifetime = _number(payload.get("expires_in"))
    if created and lifetime:
        return (created + lifetime) / 1000.0

    expires = _number(payload.get("exp"))
    if expires:
        return expires

    return time.time() + ASSUMED_TTL_SECONDS


def _jwt_payload(token: str) -> dict[str, Any] | None:
    parts = (token or "").split(".")
    if len(parts) < 2:
        return None
    raw = parts[1]
    try:
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        payload = json.loads(decoded)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def from_env() -> Session | None:
    """Credentials passed through the environment, for CI and shared runners."""
    bearer = os.environ.get(BEARER_ENV, "").strip()
    if not bearer:
        return None
    return from_headers(
        {
            protocol.AUTH_HEADER: bearer,
            protocol.API_KEY_HEADER: os.environ.get(API_KEY_ENV, ""),
            protocol.ACCOUNT_HEADER: os.environ.get(ACCOUNT_ENV, ""),
            protocol.ARP_HEADER: os.environ.get(ARP_ENV, ""),
            protocol.NONCE_HEADER: os.environ.get(NONCE_ENV, ""),
        },
        mode=MODE_MANUAL,
    )


def _from_dict(data: dict[str, Any]) -> Session:
    known = {
        "bearer",
        "api_key",
        "ims_client_id",
        "account_id",
        "arp_session_id",
        "nonce",
        "user_agent",
        "mode",
        "expires_at",
        "saved_at",
    }
    return Session(**{k: v for k, v in data.items() if k in known})


def stored() -> Session | None:
    data = store.read().get(store.SESSION_KEY)
    if not isinstance(data, dict) or not data.get("bearer"):
        return None
    return _from_dict(data)


def load() -> Session:
    """The session this invocation should use.

    The environment wins over the stored file so one invocation can pin its
    own credentials without disturbing what the CLI signed in as.
    """
    session = from_env() or stored()
    if session is None:
        raise NotLoggedIn
    return session


def save(session: Session) -> Session:
    session.saved_at = time.time()
    store.update(**{store.SESSION_KEY: session.to_dict()})
    return session


def clear() -> bool:
    if stored() is None:
        return False
    store.update(**{store.SESSION_KEY: None})
    return True
