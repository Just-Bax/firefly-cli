from __future__ import annotations

import time

import pytest
from conftest import make_token

from firefly import protocol
from firefly import session as session_store
from firefly.session import (
    MODE_MANUAL,
    Session,
    from_headers,
    headers_from_curl,
    token_expiry,
)

BASH_CURL = """
curl 'https://firefly-3p.ff.adobe.io/v2/3p-images/generate-async' \\
  -H 'accept: */*' \\
  -H 'authorization: Bearer tok123' \\
  -H 'x-api-key: clio-playground-web' \\
  -H 'x-arp-session-id: arp999' \\
  -H 'x-nonce: nonce999' \\
  -H 'x-account-id: ABC@AdobeID' \\
  --data-raw '{"prompt":"a fox"}'
"""

CMD_CURL = (
    'curl "https://firefly-3p.ff.adobe.io/v2/3p-images/generate-async" '
    '-H "authorization: Bearer tok123" '
    '-H "x-api-key: clio-playground-web"'
)


def test_token_expiry_reads_the_ims_millisecond_claims():
    expiry = token_expiry(make_token(ttl_seconds=3600))
    assert 3500 < expiry - time.time() < 3700


def test_token_expiry_falls_back_for_an_unreadable_token():
    expiry = token_expiry("not-a-jwt")
    assert 0 < expiry - time.time() <= session_store.ASSUMED_TTL_SECONDS


def test_token_expiry_is_zero_without_a_token():
    assert token_expiry("") == 0.0


def test_needs_refresh_inside_the_margin():
    fresh = Session(bearer="t", expires_at=time.time() + 86400)
    stale = Session(bearer="t", expires_at=time.time() + 60)
    assert not fresh.needs_refresh()
    assert stale.needs_refresh()


def test_pasted_credentials_are_never_auto_refreshed():
    stale = Session(bearer="t", expires_at=time.time() + 60, mode=MODE_MANUAL)
    assert stale.is_static
    assert not stale.needs_refresh()


def test_headers_carry_the_captured_set():
    session = Session(bearer="tok", account_id="A@AdobeID", arp_session_id="arp", nonce="n")
    headers = session.headers("application/json")

    assert headers[protocol.AUTH_HEADER] == "Bearer tok"
    assert headers[protocol.API_KEY_HEADER] == protocol.DEFAULT_API_KEY
    assert headers[protocol.ARP_HEADER] == "arp"
    assert headers["origin"] == protocol.WEB_ORIGIN
    assert headers["content-type"] == "application/json"


def test_headers_omit_values_that_were_never_captured():
    headers = Session(bearer="tok").headers()
    assert protocol.ARP_HEADER not in headers
    assert protocol.NONCE_HEADER not in headers
    assert "content-type" not in headers


def test_from_headers_strips_the_bearer_prefix():
    session = from_headers({"authorization": "Bearer abc", "x-api-key": "k"})
    assert session.bearer == "abc"
    assert session.api_key == "k"


@pytest.mark.parametrize("text", [BASH_CURL, CMD_CURL])
def test_headers_from_curl_reads_both_shell_flavours(text):
    headers = headers_from_curl(text)
    assert headers["authorization"] == "Bearer tok123"
    assert headers["x-api-key"] == "clio-playground-web"


def test_headers_from_curl_keeps_the_anti_abuse_pair():
    headers = headers_from_curl(BASH_CURL)
    assert headers["x-arp-session-id"] == "arp999"
    assert headers["x-nonce"] == "nonce999"
    assert headers["x-account-id"] == "ABC@AdobeID"


def test_headers_from_curl_rejects_a_block_with_no_headers():
    with pytest.raises(ValueError, match="No -H headers"):
        headers_from_curl("curl https://example.invalid")


def test_save_load_roundtrip(session):
    session_store.save(session)
    loaded = session_store.load()
    assert loaded.bearer == session.bearer
    assert loaded.arp_session_id == session.arp_session_id


def test_the_environment_wins_over_the_stored_file(session, monkeypatch):
    session_store.save(session)
    monkeypatch.setenv("FIREFLY_BEARER", "from-env")
    loaded = session_store.load()
    assert loaded.bearer == "from-env"
    assert loaded.is_static


def test_redacted_never_shows_the_token(session):
    rendered = str(session.redacted())
    assert session.bearer not in rendered
    assert "arp-value" not in rendered


def test_clear_removes_the_session(session):
    session_store.save(session)
    assert session_store.clear()
    assert session_store.stored() is None
