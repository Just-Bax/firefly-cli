from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import protocol
from .errors import LoginFailed
from .paths import browser_profile_dir
from .session import MODE_BROWSER, Session, from_headers

LOGIN_WAIT_MS = 5 * 60 * 1000
REFRESH_WAIT_MS = 90 * 1000
POLL_INTERVAL_SECONDS = 0.5
PROGRESS_EVERY_SECONDS = 5.0

# A page load fires several authenticated calls. Once one arrives, wait this
# long for a better one before settling, since the first is often a profile
# fetch that carries the bearer but not the anti-abuse headers.
SETTLE_SECONDS = 6.0

_MISSING_BROWSER_HINTS = ("executable doesn't exist", "please run the following command")

_CAPTURE_HOST_SUFFIX = ".adobe.io"

Progress = Callable[[str], None]


@dataclass
class Capture:
    """One authenticated request the web app made, as seen from outside."""

    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""

    def score(self) -> int:
        return sum(1 for name in protocol.SESSION_HEADERS if self.headers.get(name))

    def complete(self) -> bool:
        """Carries the headers that only a real generation sends."""
        return self.usable() and all(self.headers.get(name) for name in protocol.GENERATE_HEADERS)

    def usable(self) -> bool:
        if not self.headers.get(protocol.API_KEY_HEADER):
            return False
        return protocol.looks_like_token(self.headers.get(protocol.AUTH_HEADER, ""))


def harvest(
    headless: bool = False,
    timeout_ms: int = LOGIN_WAIT_MS,
    url: str = protocol.IMAGE_APP_URL,
    on_progress: Progress | None = None,
    require_complete: bool = False,
) -> Session:
    """Open the Firefly web app and come back with the headers it authenticates with.

    Credentials never pass through this process: the user signs in to the real
    Adobe page, which is what makes Google SSO and MFA work unchanged. What is
    taken afterwards is only what the app already sends on every call.

    Being signed in is decided by seeing an authenticated request, not by
    reading the address bar, because the SSO redirect chain runs through domains
    that are not ours to recognise.
    """
    report: Progress = on_progress or (lambda _message: None)
    if require_complete:
        report("waiting for a generation: type a prompt and press Generate once")
    captures = _run(
        url, headless, timeout_ms, report, stop_when_usable=True, require_complete=require_complete
    )

    best = _best(captures)
    if best is None:
        raise LoginFailed(_nothing_captured(headless, url, captures))
    if require_complete and not best.complete():
        missing = [name for name in protocol.GENERATE_HEADERS if not best.headers.get(name)]
        raise LoginFailed(
            f"Signed in, but no generation was seen, so {', '.join(missing)} is missing.\n"
            "Adobe refuses every generate request without those, reporting it as "
            "'system under load', so a session without them is useless.\n\n"
            "Re-run and press Generate once in the page, then wait for the image.\n"
            "Or use 'firefly login --curl', copying a generate call from your own browser."
        )

    session = from_headers(best.headers, mode=MODE_BROWSER)
    if not session.bearer:
        raise LoginFailed("Captured a request from Adobe, but it carried no bearer token.")
    return session


def refresh(session: Session) -> Session:
    """Renew an expired token without the user.

    Adobe's own sign-in cookie in the persistent profile outlives the one-day
    IMS token by a wide margin, so loading the app headless mints a fresh one.
    """
    renewed = harvest(headless=True, timeout_ms=REFRESH_WAIT_MS)
    session.bearer = renewed.bearer
    session.expires_at = renewed.expires_at
    session.api_key = renewed.api_key or session.api_key
    session.ims_client_id = renewed.ims_client_id or session.ims_client_id
    session.account_id = renewed.account_id or session.account_id
    session.user_agent = renewed.user_agent or session.user_agent
    # These two rotate with the browser session rather than the token, so an
    # empty capture must not blank a pair that still works.
    session.arp_session_id = renewed.arp_session_id or session.arp_session_id
    session.nonce = renewed.nonce or session.nonce
    return session


def capture_traffic(
    url: str,
    timeout_ms: int = LOGIN_WAIT_MS,
    on_progress: Progress | None = None,
) -> list[Capture]:
    """Record every authenticated call the app makes while the user drives it.

    This is how an endpoint that has not been mapped yet gets mapped: generate
    once by hand, then read back what was actually sent.
    """
    report: Progress = on_progress or (lambda _message: None)
    return _run(url, False, timeout_ms, report, stop_when_usable=False)


def _run(
    url: str,
    headless: bool,
    timeout_ms: int,
    report: Progress,
    stop_when_usable: bool,
    require_complete: bool = False,
) -> list[Capture]:
    playwright = _import_playwright()
    pending: list[Any] = []
    captures: list[Capture] = []

    with playwright() as p:
        context = _launch(p, headless)
        try:
            context.on("request", lambda request: _queue(request, pending))
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as exc:
                raise LoginFailed(f"Could not open {url}: {str(exc).splitlines()[0]}") from exc

            _wait(
                context, pending, captures, timeout_ms, report, stop_when_usable, require_complete
            )
            _drain(pending, captures)
        finally:
            _close(context)

    return captures


def _queue(request: Any, pending: list[Any]) -> None:
    """Note an interesting request and get out of the event handler.

    Reading the full header set needs a round trip to the browser, which cannot
    be made from inside a sync-API event callback, so that is left to _drain.
    """
    try:
        if _CAPTURE_HOST_SUFFIX in request.url.split("/")[2]:
            pending.append(request)
    except Exception:
        return


def _drain(pending: list[Any], captures: list[Capture]) -> None:
    """Turn queued requests into captures, from the main thread.

    all_headers() is what carries authorization: the plain headers property
    omits anything the browser classes as security-related, which is exactly
    the set being harvested here.
    """
    while pending:
        request = pending.pop(0)
        headers = _headers_of(request)
        if not any(headers.get(name) for name in protocol.REQUIRED_HEADERS):
            continue

        try:
            body = request.post_data or ""
        except Exception:
            body = ""

        captures.append(
            Capture(
                method=getattr(request, "method", "GET"),
                url=getattr(request, "url", ""),
                headers=headers,
                body=body[:20000],
            )
        )


def _headers_of(request: Any) -> dict[str, str]:
    for reader in (lambda: request.all_headers(), lambda: request.headers):
        try:
            return {k.lower(): v for k, v in reader().items()}
        except Exception:
            continue
    return {}


def _wait(
    context: Any,
    pending: list[Any],
    captures: list[Capture],
    timeout_ms: int,
    report: Progress,
    stop_when_usable: bool,
    require_complete: bool = False,
) -> None:
    started = time.monotonic()
    deadline = started + timeout_ms / 1000
    next_report = started + PROGRESS_EVERY_SECONDS
    first_usable = 0.0
    told_to_generate = False

    while time.monotonic() < deadline:
        pages = _open_pages(context)
        if not pages:
            if stop_when_usable and _best(captures) is None:
                raise LoginFailed("The browser window was closed before sign-in finished.")
            return

        _drain(pending, captures)
        best = _best(captures)
        if stop_when_usable and best is not None:
            if first_usable == 0.0:
                first_usable = time.monotonic()
                report("signed in, reading the app's own request headers")
            settled = time.monotonic() - first_usable >= SETTLE_SECONDS
            if best.complete() or (settled and not require_complete):
                return

        now = time.monotonic()
        if now >= next_report:
            next_report = now + PROGRESS_EVERY_SECONDS
            waited = int(now - started)
            usable = sum(1 for c in captures if c.usable())
            report(f"waiting {waited}s: {len(captures)} adobe call(s), {usable} usable")
            # A cold profile lands on the sign-in page and makes no API calls at
            # all until the user does something, so say so rather than time out
            # silently.
            if not captures and waited >= 30 and not told_to_generate:
                told_to_generate = True
                report("still nothing: sign in, then press Generate once in the page")

        _pause(pages)


def _pause(pages: list[Any]) -> None:
    """Wait a beat between rounds, through Playwright rather than around it.

    The sync API only delivers browser events while the caller is inside one of
    its calls. A plain time.sleep never yields to it, so requests made while we
    sleep are never handed to the listener.
    """
    for page in pages:
        try:
            page.wait_for_timeout(POLL_INTERVAL_SECONDS * 1000)
            return
        except Exception:
            continue
    time.sleep(POLL_INTERVAL_SECONDS)


def _best(captures: list[Capture]) -> Capture | None:
    """The richest capture, and among equals the most recent.

    Only the generate call carries the anti-abuse headers, so a richer one turns
    up long after the page has settled. Ties break towards the newest because
    its token has the most life left.
    """
    usable = [(index, c) for index, c in enumerate(captures) if c.usable()]
    if not usable:
        return None
    return max(usable, key=lambda pair: (pair[1].score(), pair[0]))[1]


def _open_pages(context: Any) -> list[Any]:
    live = []
    for page in context.pages:
        try:
            if not page.is_closed():
                live.append(page)
        except Exception:
            continue
    return live


def _nothing_captured(headless: bool, url: str, captures: list[Capture]) -> str:
    if headless:
        return (
            "The stored browser profile is no longer signed in to Adobe, so the token "
            "could not be renewed.\nRun 'firefly login' and sign in again."
        )

    seen = ""
    if captures:
        richest = max(captures, key=lambda c: len(c.headers))
        wanted = [name for name in protocol.SESSION_HEADERS if richest.headers.get(name)]
        seen = (
            f"\n{len(captures)} call(s) to Adobe were seen, but none carried both "
            f"{' and '.join(protocol.REQUIRED_HEADERS)}.\n"
            f"Best had: {', '.join(wanted) or 'none of the headers looked for'}."
        )

    return (
        f"Signed in at {url}, but no usable request to Adobe was seen.{seen}\n"
        "Type any prompt and press Generate once, which forces the app to call the API.\n\n"
        "If Google said this browser may not be secure, it refused to sign in because it "
        "detected automation. Two ways past it:\n"
        "  firefly login --curl        sign in with your normal browser, paste one request\n"
        "  firefly config set browser_channel msedge    then re-run 'firefly login'"
    )


def _import_playwright() -> Any:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise LoginFailed(
            "Playwright is not installed.\n"
            "Run: pip install playwright && playwright install chromium"
        ) from None
    return sync_playwright


def _launch(p: Any, headless: bool = False) -> Any:
    from .config import load as load_config

    config = load_config()
    options: dict[str, Any] = {
        "user_data_dir": str(_profile_dir(config.browser_profile)),
        "headless": headless,
        "args": [
            "--no-first-run",
            "--no-default-browser-check",
            # Google refuses its sign-in page when it can see the automation
            # flag, which is the "this browser may not be secure" refusal.
            "--disable-blink-features=AutomationControlled",
        ],
        "ignore_default_args": ["--enable-automation"],
        "viewport": {"width": 1600, "height": 900},
    }
    if config.browser_path:
        options["executable_path"] = config.browser_path
    elif config.browser_channel:
        options["channel"] = config.browser_channel

    try:
        return p.chromium.launch_persistent_context(**options)
    except Exception as exc:
        if _looks_like_missing_browser(exc):
            raise LoginFailed("The sign-in browser is not installed.\nRun: firefly setup") from exc
        if _looks_like_busy_profile(exc):
            which = config.browser_channel or "a browser"
            raise LoginFailed(
                f"That browser profile is already open in {which}.\n"
                "Close every window of it and try again."
            ) from exc
        raise LoginFailed(f"Could not start the browser: {exc}") from exc


def _profile_dir(configured: str) -> Path:
    return Path(configured).expanduser() if configured else browser_profile_dir()


def _looks_like_busy_profile(exc: Exception) -> bool:
    message = str(exc).lower()
    return "profile" in message and ("in use" in message or "lock" in message)


def _close(context: Any) -> None:
    try:
        context.close()
    except Exception:
        pass


def _looks_like_missing_browser(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(hint in message for hint in _MISSING_BROWSER_HINTS)


def install_chromium() -> bool:
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=False,
    )
    return result.returncode == 0


def _browsers_root() -> Path:
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override:
        return Path(override)
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(local) / "ms-playwright"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def browser_is_installed() -> bool:
    """Look for the unpacked browser on disk. Asking Playwright itself would
    start its driver, which prints teardown noise on a plain status check."""
    root = _browsers_root()
    return root.is_dir() and any(root.glob("chromium*"))
