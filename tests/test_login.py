from __future__ import annotations

import pytest
from conftest import make_token

from firefly import login as login_module
from firefly import protocol
from firefly.config import Config
from firefly.errors import LoginFailed
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


def build(root, name, complete=True):
    directory = root / name
    directory.mkdir(parents=True)
    if complete:
        (directory / "INSTALLATION_COMPLETE").touch()
    return directory


def pin(monkeypatch, root, builds=("chromium-1234", "chromium_headless_shell-1234")):
    monkeypatch.setattr(login_module, "_browsers_root", lambda: root)
    monkeypatch.setattr(login_module, "_pinned_builds", lambda: list(builds))


def test_a_complete_install_of_every_pinned_build_is_installed(tmp_path, monkeypatch):
    pin(monkeypatch, tmp_path)
    build(tmp_path, "chromium-1234")
    build(tmp_path, "chromium_headless_shell-1234")

    assert login_module.browser_is_installed()


def test_a_browser_from_another_project_is_not_this_one(tmp_path, monkeypatch):
    # The bug this guards: any chromium* directory counted, so a build left by
    # some other tool reported ready and then failed to launch.
    pin(monkeypatch, tmp_path)
    build(tmp_path, "chromium-1000")
    build(tmp_path, "chromium_headless_shell-1000")

    assert not login_module.browser_is_installed()


def test_an_interrupted_download_is_not_installed(tmp_path, monkeypatch):
    pin(monkeypatch, tmp_path)
    build(tmp_path, "chromium-1234", complete=False)
    build(tmp_path, "chromium_headless_shell-1234")

    assert not login_module.browser_is_installed()


def test_the_headless_shell_is_required_too(tmp_path, monkeypatch):
    # Renewing the token runs headless, which uses the shell rather than chrome.
    pin(monkeypatch, tmp_path)
    build(tmp_path, "chromium-1234")

    assert not login_module.browser_is_installed()


def test_an_unreadable_manifest_counts_as_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(login_module, "_browsers_root", lambda: tmp_path)
    monkeypatch.setattr(
        login_module, "_pinned_builds", lambda: (_ for _ in ()).throw(OSError("no manifest"))
    )

    assert not login_module.browser_is_installed()


def test_the_pinned_builds_come_from_playwrights_own_manifest():
    builds = login_module._pinned_builds()

    assert builds, "playwright ships a browsers.json listing the builds it pins"
    assert any(name.startswith("chromium-") for name in builds)
    assert any(name.startswith("chromium_headless_shell-") for name in builds)


class FakeChromium:
    """Fails to launch until the browser is fetched, like a real missing build."""

    def __init__(self, failures=1, error="Executable doesn't exist at chrome.exe"):
        self.failures = failures
        self.error = error
        self.attempts = 0

    def launch_persistent_context(self, **_options):
        self.attempts += 1
        if self.attempts <= self.failures:
            raise RuntimeError(self.error)
        return "context"


class FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium


def use_config(monkeypatch, **overrides):
    config = Config(**overrides)
    monkeypatch.setattr("firefly.config.load", lambda: config)
    return config


def test_login_fetches_the_browser_and_retries_rather_than_giving_up(monkeypatch):
    # The dead end this guards: launch failed telling the user to run setup,
    # while setup believed the browser was already there.
    use_config(monkeypatch)
    fetched = []
    monkeypatch.setattr(login_module, "install_chromium", lambda: fetched.append(True) or True)
    chromium = FakeChromium(failures=1)

    context = login_module._launch(FakePlaywright(chromium), headless=True)

    assert context == "context"
    assert fetched == [True]
    assert chromium.attempts == 2


def test_a_failed_download_says_so_rather_than_looping(monkeypatch):
    use_config(monkeypatch)
    monkeypatch.setattr(login_module, "install_chromium", lambda: False)

    with pytest.raises(LoginFailed, match="could not be downloaded"):
        login_module._launch(FakePlaywright(FakeChromium(failures=1)))


def test_a_missing_system_browser_is_not_fixed_by_downloading_chromium(monkeypatch):
    # channel names a browser Google trusts; fetching Chromium would not help.
    use_config(monkeypatch, browser_channel="msedge")
    monkeypatch.setattr(login_module, "install_chromium", lambda: pytest.fail("must not download"))

    with pytest.raises(LoginFailed, match="msedge"):
        login_module._launch(FakePlaywright(FakeChromium(failures=1)))


def test_an_unrelated_launch_failure_is_not_treated_as_a_missing_browser(monkeypatch):
    use_config(monkeypatch)
    monkeypatch.setattr(login_module, "install_chromium", lambda: pytest.fail("must not download"))
    chromium = FakeChromium(failures=1, error="Target page crashed")

    with pytest.raises(LoginFailed, match="Could not start the browser"):
        login_module._launch(FakePlaywright(chromium))
