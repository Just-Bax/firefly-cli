from __future__ import annotations

from conftest import make_token

from firefly import protocol
from firefly.login import Capture, _best


def capture(auth: str, **extra: str) -> Capture:
    headers = {protocol.API_KEY_HEADER: "clio-playground-web", "user-agent": "chrome"}
    if auth:
        headers[protocol.AUTH_HEADER] = auth
    headers.update(extra)
    return Capture(method="GET", url="https://x.adobe.io/y", headers=headers)


def test_a_header_present_but_empty_is_not_a_token():
    # The app fires its first calls before the token resolves.
    assert not protocol.looks_like_token("Bearer")
    assert not protocol.looks_like_token("Bearer ")
    assert not protocol.looks_like_token("")
    assert not protocol.looks_like_token("Bearer not-a-jwt")


def test_a_real_ims_token_is_recognised():
    assert protocol.looks_like_token(f"Bearer {make_token()}")
    assert protocol.looks_like_token(make_token())


def test_a_capture_without_a_real_token_is_unusable():
    assert not capture("Bearer").usable()
    assert not capture("").usable()
    assert capture(f"Bearer {make_token()}").usable()


def test_a_capture_without_an_api_key_is_unusable():
    bare = Capture(
        method="GET",
        url="https://x.adobe.io/y",
        headers={protocol.AUTH_HEADER: f"Bearer {make_token()}"},
    )
    assert not bare.usable()


def test_best_skips_the_half_built_request():
    good = capture(f"Bearer {make_token()}")
    assert _best([capture("Bearer"), good]) is good


def test_best_prefers_the_capture_carrying_the_anti_abuse_headers():
    plain = capture(f"Bearer {make_token()}")
    rich = capture(
        f"Bearer {make_token()}",
        **{
            protocol.ARP_HEADER: "arp",
            protocol.NONCE_HEADER: "nonce",
            protocol.ACCOUNT_HEADER: "A@AdobeID",
        },
    )
    assert _best([plain, rich]) is rich
    assert _best([rich, plain]) is rich


def test_best_breaks_a_tie_towards_the_newest():
    first = capture(f"Bearer {make_token()}")
    second = capture(f"Bearer {make_token()}")
    assert _best([first, second]) is second


def test_best_is_none_when_nothing_was_authenticated():
    assert _best([]) is None
    assert _best([capture("")]) is None


def test_complete_needs_the_generate_only_headers():
    plain = capture(f"Bearer {make_token()}")
    assert plain.usable()
    assert not plain.complete()

    full = capture(
        f"Bearer {make_token()}",
        **{protocol.ARP_HEADER: "arp", protocol.NONCE_HEADER: "nonce"},
    )
    assert full.complete()


def test_complete_does_not_require_the_account_header():
    # The real generate call does not send x-account-id, so requiring it would
    # make a successful login impossible.
    full = capture(
        f"Bearer {make_token()}",
        **{protocol.ARP_HEADER: "arp", protocol.NONCE_HEADER: "nonce"},
    )
    assert not full.headers.get(protocol.ACCOUNT_HEADER)
    assert full.complete()


def test_complete_is_false_with_only_one_of_the_pair():
    assert not capture(f"Bearer {make_token()}", **{protocol.ARP_HEADER: "arp"}).complete()
    assert not capture(f"Bearer {make_token()}", **{protocol.NONCE_HEADER: "n"}).complete()


def test_complete_is_false_without_a_real_token():
    half = capture(
        "Bearer",
        **{protocol.ARP_HEADER: "arp", protocol.NONCE_HEADER: "nonce"},
    )
    assert not half.complete()
