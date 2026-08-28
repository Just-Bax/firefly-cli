from __future__ import annotations

import httpx
import pytest
from conftest import ASSET_URL, PNG, RESULT_URL, Recorder, json_response, make_token

from firefly import protocol
from firefly.client import Client
from firefly.errors import ApiError, NotFound, Rejected, SessionExpired
from firefly.session import MODE_MANUAL, Session


def build(routes, session, **kwargs):
    recorder = Recorder(routes)
    return Client(session, transport=recorder.transport(), **kwargs), recorder


def test_upload_returns_the_blob_id(client):
    assert client.upload(PNG, "image/png") == "blob-9"


def test_upload_sends_the_image_content_type(client, happy_path):
    client.upload(PNG, "image/jpeg")
    request = happy_path.sent_to(protocol.UPLOAD_PATH)[0]
    assert request.headers["content-type"] == "image/jpeg"
    assert request.content == PNG


def test_create_returns_the_links_verbatim(client):
    result_url, cancel_url, _ = client.create(protocol.IMAGE_PATH, {"prompt": "x"})
    assert result_url == RESULT_URL
    assert "cancel" in cancel_url


def test_poll_uses_the_region_scoped_url(client, happy_path):
    client.poll(RESULT_URL)
    assert happy_path.sent_to(RESULT_URL)


def test_408_is_retried_then_succeeds(session):
    attempts = {"count": 0}

    def flaky(_request):
        attempts["count"] += 1
        if attempts["count"] < 3:
            return json_response(408, {"message": "system under load"})
        return json_response(200, {"images": [{"id": "blob-9"}]})

    client, _ = build([(protocol.UPLOAD_PATH, flaky)], session)
    assert client.upload(PNG) == "blob-9"
    assert attempts["count"] == 3


def test_retries_give_up_and_report(session):
    client, _ = build(
        [(protocol.UPLOAD_PATH, json_response(503, {"message": "down"}))], session, max_retries=2
    )
    with pytest.raises(ApiError) as caught:
        client.upload(PNG)
    assert caught.value.status == 503


def test_401_triggers_one_refresh_then_retries(session):
    attempts = {"count": 0}
    refreshed = {"count": 0}

    def once_unauthorised(_request):
        attempts["count"] += 1
        if attempts["count"] == 1:
            return json_response(401, {"message": "expired"})
        return json_response(200, {"images": [{"id": "blob-9"}]})

    def renew(current):
        refreshed["count"] += 1
        current.bearer = make_token()
        return current

    client, _ = build([(protocol.UPLOAD_PATH, once_unauthorised)], session, on_refresh=renew)
    assert client.upload(PNG) == "blob-9"
    assert refreshed["count"] == 1


def test_401_twice_is_reported_rather_than_looping(session):
    def always_unauthorised(_request):
        return json_response(401, {"message": "expired"})

    client, _ = build(
        [(protocol.UPLOAD_PATH, always_unauthorised)],
        session,
        on_refresh=lambda current: current,
    )
    with pytest.raises(SessionExpired):
        client.upload(PNG)


def test_pasted_credentials_are_not_refreshed(session):
    session.mode = MODE_MANUAL
    client, _ = build([(protocol.UPLOAD_PATH, json_response(401, {}))], session)
    with pytest.raises(SessionExpired, match="login --curl"):
        client.upload(PNG)


def test_403_is_a_refusal_not_an_expiry(session):
    client, _ = build(
        [(protocol.IMAGE_PATH, json_response(403, {"message": "content policy"}))], session
    )
    with pytest.raises(Rejected, match="content policy"):
        client.create(protocol.IMAGE_PATH, {"prompt": "x"})


def test_404_says_the_endpoint_may_have_moved(session):
    client, _ = build([], session)
    with pytest.raises(NotFound):
        client.create("/v2/nowhere", {"prompt": "x"})


def test_a_relative_path_resolves_against_the_firefly_host(session):
    client, recorder = build([(protocol.UPLOAD_PATH, json_response(200, {"id": "b"}))], session)
    client.upload(PNG)
    assert str(recorder.requests[0].url) == f"{protocol.HOST}{protocol.UPLOAD_PATH}"


def test_fetch_does_not_send_the_bearer_to_a_presigned_url(client, happy_path):
    assert client.fetch(ASSET_URL) == PNG
    request = happy_path.sent_to("cdn.example.invalid")[0]
    assert protocol.AUTH_HEADER not in request.headers
    assert protocol.API_KEY_HEADER not in request.headers


def test_a_transport_failure_becomes_a_reportable_error(session):
    def explode(_request):
        raise httpx.ConnectError("no route to host")

    client = Client(session, transport=httpx.MockTransport(explode))
    with pytest.raises(Exception) as caught:
        client.upload(PNG)
    assert "Could not reach" in str(caught.value)


def test_a_missing_blob_id_is_reported(session):
    client, _ = build([(protocol.UPLOAD_PATH, json_response(200, {"ok": True}))], session)
    with pytest.raises(ApiError, match="no asset id"):
        client.upload(PNG)


def test_a_create_without_links_is_reported(session):
    client, _ = build([(protocol.IMAGE_PATH, json_response(202, {"status": "ok"}))], session)
    with pytest.raises(ApiError, match="no result link"):
        client.create(protocol.IMAGE_PATH, {"prompt": "x"})


def test_a_session_that_needs_refresh_is_renewed_before_the_first_call():
    stale = Session(bearer="old", expires_at=1.0)
    renewed = {"count": 0}

    def renew(current):
        renewed["count"] += 1
        current.bearer = make_token()
        current.expires_at = 0.0
        return current

    client, _ = build(
        [(protocol.UPLOAD_PATH, json_response(200, {"id": "b"}))], stale, on_refresh=renew
    )
    client.upload(PNG)
    assert renewed["count"] == 1
