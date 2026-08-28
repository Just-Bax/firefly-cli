from __future__ import annotations

import base64
import json
import time

import httpx
import pytest

from firefly import protocol
from firefly.client import Client
from firefly.config import Config
from firefly.paths import HOME_ENV
from firefly.service import FireflyService
from firefly.session import Session

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 32

RESULT_URL = "https://firefly-3p-ue1.ff.adobe.io/v2/status/job-abc123"
CANCEL_URL = "https://firefly-3p-ue1.ff.adobe.io/v2/cancel/job-abc123"
ASSET_URL = "https://cdn.example.invalid/out.png?sig=x"

DISCOVERY = {
    "models": [
        {
            "modelId": "gemini-flash",
            "modelVersions": {
                "nano-banana-3": {
                    "outputModality": ["image"],
                    "modelDisplayName": "Gemini 3.1",
                    "enabled": True,
                },
                "nano-banana": {
                    "outputModality": ["image"],
                    "modelDisplayName": "Gemini 2.5",
                    "enabled": False,
                },
            },
        },
        {
            "modelId": "veo",
            "modelVersions": {
                "3.1-generate": {
                    "outputModality": ["video"],
                    "modelDisplayName": "Veo 3.1",
                    "enabled": True,
                },
                "2.0-generate": {
                    "outputModality": ["video"],
                    "modelDisplayName": "Veo 2",
                    "enabled": False,
                },
            },
        },
        {
            "modelId": "elevenlabs",
            "modelVersions": {"eleven_v3": {"outputModality": ["audio"], "enabled": True}},
        },
    ]
}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Every test gets its own ~/.firefly, so nothing touches the real one."""
    monkeypatch.setenv(HOME_ENV, str(tmp_path / "home"))
    for name in (
        "FIREFLY_BEARER",
        "FIREFLY_API_KEY",
        "FIREFLY_ACCOUNT_ID",
        "FIREFLY_ARP_SESSION_ID",
        "FIREFLY_NONCE",
    ):
        monkeypatch.delenv(name, raising=False)
    return tmp_path / "home"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """The retry backoff is real seconds; tests should not pay them."""
    monkeypatch.setattr("firefly.client.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("firefly.service.time.sleep", lambda _seconds: None)


def make_token(ttl_seconds: int = 86400) -> str:
    payload = {
        "client_id": "clio-playground-web",
        "created_at": str(int(time.time() * 1000)),
        "expires_in": str(ttl_seconds * 1000),
    }
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{raw}.signature"


@pytest.fixture
def session():
    return Session(
        bearer=make_token(),
        api_key=protocol.DEFAULT_API_KEY,
        ims_client_id=protocol.DEFAULT_API_KEY,
        account_id="ABC@AdobeID",
        arp_session_id="arp-value",
        nonce="nonce-value",
        expires_at=time.time() + 86400,
    )


class Recorder:
    """A MockTransport that answers by URL and remembers what was asked."""

    def __init__(self, routes):
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for match, responder in self.routes:
            if match in str(request.url):
                return responder(request) if callable(responder) else responder
        return httpx.Response(404, json={"message": f"no route for {request.url}"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def sent_to(self, match: str) -> list[httpx.Request]:
        return [r for r in self.requests if match in str(r.url)]


def json_response(status: int, payload) -> httpx.Response:
    return httpx.Response(status, json=payload)


@pytest.fixture
def happy_path():
    """Upload, submit, one poll that is still running, then a finished one."""
    polls = {"count": 0}

    def poll(_request):
        polls["count"] += 1
        if polls["count"] < 2:
            return json_response(200, {"status": "IN_PROGRESS", "progress": 40})
        return json_response(
            200,
            {
                "status": "SUCCEEDED",
                "progress": 100,
                "outputs": [{"image": {"id": "img-1", "presignedUrl": ASSET_URL}}],
            },
        )

    return Recorder(
        [
            (protocol.UPLOAD_PATH, json_response(200, {"images": [{"id": "blob-9"}]})),
            (
                protocol.IMAGE_PATH,
                json_response(
                    202,
                    {
                        "links": {
                            "result": {"href": RESULT_URL},
                            "cancel": {"href": CANCEL_URL},
                        }
                    },
                ),
            ),
            ("/v2/status/", poll),
            ("cdn.example.invalid", httpx.Response(200, content=PNG)),
        ]
    )


@pytest.fixture
def finished_path():
    """Answers a poll as already finished, for flows that submit and collect
    in separate steps."""
    return Recorder(
        [
            (
                protocol.IMAGE_PATH,
                json_response(202, {"links": {"result": {"href": RESULT_URL}}}),
            ),
            (
                "/v2/status/",
                json_response(
                    200,
                    {
                        "status": "SUCCEEDED",
                        "outputs": [{"image": {"presignedUrl": ASSET_URL}}],
                    },
                ),
            ),
            ("cdn.example.invalid", httpx.Response(200, content=PNG)),
        ]
    )


@pytest.fixture
def client(session, happy_path):
    with Client(session, transport=happy_path.transport()) as built:
        yield built


@pytest.fixture
def service(client):
    return FireflyService(client, Config(poll_seconds=0))
