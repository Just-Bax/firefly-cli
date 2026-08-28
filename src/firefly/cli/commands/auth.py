from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ... import login as login_module
from ... import protocol
from ... import session as session_store
from ...errors import LoginFailed
from ...session import MODE_MANUAL, Session, from_headers, headers_from_curl
from ..context import Context
from ..output import build_table, emit, note

CURL_HELP = """\
Paste a request copied from the browser: open firefly.adobe.com, press F12,
generate once, right-click the call to firefly-3p.ff.adobe.io/v2/3p-images in
the Network tab and choose Copy > Copy as cURL. Paste it, then press Ctrl-Z
(Windows) or Ctrl-D and Enter.
"""

BROWSER_HELP = """\
Opening Adobe Firefly. Sign in, then type any prompt and press Generate once.

That generation is not optional: Adobe sends two anti-abuse headers only with a
real one, and refuses everything without them.\
"""


def register(subparsers: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    login = subparsers.add_parser(
        "login",
        parents=[common],
        help="sign in to Adobe Firefly",
        description=(
            "Opens the real Adobe sign-in page in a browser and keeps the headers the "
            "Firefly app authenticates with. Use --curl to paste them instead."
        ),
    )
    login.add_argument(
        "--curl",
        nargs="?",
        const="-",
        metavar="FILE",
        help=(
            "read a 'Copy as cURL' block instead of opening a browser: from FILE, "
            "or from stdin when given no value"
        ),
    )
    login.add_argument("--bearer", metavar="TOKEN", help="set the IMS token directly")
    login.add_argument("--api-key", metavar="KEY", help=f"defaults to {protocol.DEFAULT_API_KEY}")
    login.add_argument("--account-id", metavar="ID", help="the x-account-id header")
    login.add_argument("--arp-session-id", metavar="ID", help="the x-arp-session-id header")
    login.add_argument("--nonce", metavar="NONCE", help="the x-nonce header")
    login.add_argument("--headless", action="store_true", help="do not show the browser window")
    login.add_argument(
        "--quick",
        action="store_true",
        help="take just the token, without waiting for a generation (renewal only)",
    )
    login.set_defaults(func=cmd_login)

    logout = subparsers.add_parser("logout", parents=[common], help="forget the stored session")
    logout.set_defaults(func=cmd_logout)

    whoami = subparsers.add_parser(
        "whoami", parents=[common], help="show the stored session and when it expires"
    )
    whoami.set_defaults(func=cmd_whoami)


def cmd_login(ctx: Context) -> int:
    args = ctx.args
    if args.curl:
        session = _from_curl(ctx)
    elif args.bearer:
        session = _from_flags(ctx)
    else:
        session = _from_browser(ctx)

    session_store.save(session)
    data = session.redacted()
    emit(ctx, data, lambda: _table(data))
    if not ctx.as_json:
        note(ctx, '\n[green]Signed in.[/green] Try: firefly image "a red fox in snow"')
    return 0


def _from_browser(ctx: Context) -> Session:
    if not login_module.browser_is_installed():
        raise LoginFailed("The sign-in browser is not installed yet. Run: firefly setup")

    note(ctx, BROWSER_HELP if not ctx.args.quick else "Opening Adobe Firefly to renew the token.")
    return login_module.harvest(
        headless=ctx.args.headless,
        on_progress=lambda message: note(ctx, f"[dim]{message}[/dim]"),
        require_complete=not ctx.args.quick,
    )


def _from_curl(ctx: Context) -> Session:
    source = ctx.args.curl
    if source == "-":
        note(ctx, CURL_HELP)
        text = sys.stdin.read()
    else:
        path = Path(source).expanduser()
        if not path.is_file():
            raise LoginFailed(f"No such file: {path}")
        text = path.read_text(encoding="utf-8", errors="replace")

    if not text.strip():
        raise LoginFailed("Nothing was pasted.")

    try:
        headers = headers_from_curl(text)
    except ValueError as exc:
        raise LoginFailed(str(exc)) from None

    missing = [h for h in protocol.REQUIRED_HEADERS if not headers.get(h)]
    if missing:
        raise LoginFailed(
            f"That request is missing {', '.join(missing)}. Copy a call to "
            "firefly-3p.ff.adobe.io rather than one to the page itself."
        )
    return from_headers(headers, mode=MODE_MANUAL)


def _from_flags(ctx: Context) -> Session:
    args = ctx.args
    return from_headers(
        {
            protocol.AUTH_HEADER: args.bearer or "",
            protocol.API_KEY_HEADER: args.api_key or "",
            protocol.ACCOUNT_HEADER: args.account_id or "",
            protocol.ARP_HEADER: args.arp_session_id or "",
            protocol.NONCE_HEADER: args.nonce or "",
        },
        mode=MODE_MANUAL,
    )


def cmd_logout(ctx: Context) -> int:
    removed = session_store.clear()
    emit(
        ctx,
        {"signed_out": removed},
        text="Signed out." if removed else "There was no stored session.",
    )
    return 0


def cmd_whoami(ctx: Context) -> int:
    session = session_store.stored() or session_store.from_env()
    if session is None:
        emit(ctx, {"signed_in": False}, text="Not signed in. Run 'firefly login'.")
        return 3

    data = session.redacted()
    data["signed_in"] = True
    data["browser_profile"] = login_module.browser_is_installed()
    emit(ctx, data, lambda: _table(data))
    return 0


def _table(data: dict) -> object:
    return build_table(
        None,
        [{"header": "Field", "style": "cyan"}, {"header": "Value", "overflow": "fold"}],
        [[key, "-" if value is None else str(value)] for key, value in data.items()],
    )
