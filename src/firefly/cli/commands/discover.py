from __future__ import annotations

import argparse
import json
import time
from typing import Any
from urllib.parse import urlsplit

from ... import config as config_module
from ... import login as login_module
from ... import protocol
from ...errors import FireflyError
from ...paths import capture_dir, restrict
from ..context import Context
from ..output import build_table, emit, note

INTRO = """\
A browser will open. Generate ONE {kind} by hand, wait for it to finish, then
close the window. Everything the app sent is recorded, and the endpoint it used
is written to your settings.
"""

_SECRET_HEADERS = (protocol.AUTH_HEADER, protocol.ARP_HEADER, protocol.NONCE_HEADER)


def register(subparsers: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    group = subparsers.add_parser(
        "discover",
        parents=[common],
        help="record what the web app calls, to map an endpoint that moved",
        description=(
            "Use this when a command starts returning 404, which means Adobe has "
            "redeployed and moved something."
        ),
    )
    group.add_argument(
        "kind", nargs="?", default="image", choices=("image", "video"), help="which page to open"
    )
    group.add_argument(
        "--no-save", action="store_true", help="report findings without changing settings"
    )
    group.set_defaults(func=cmd_discover)


def cmd_discover(ctx: Context) -> int:
    kind = ctx.args.kind
    url = protocol.VIDEO_APP_URL if kind == "video" else protocol.IMAGE_APP_URL

    note(ctx, INTRO.format(kind=kind))
    captures = login_module.capture_traffic(
        url, on_progress=lambda message: note(ctx, f"[dim]{message}[/dim]")
    )
    if not captures:
        raise FireflyError(
            "Nothing was recorded. The browser needs to be signed in, and a generation "
            "has to actually run before there is anything to see."
        )

    dump = _write_dump(captures, kind)
    generate = _generation_call(captures)
    found: dict[str, Any] = {
        "captured": len(captures),
        "dump": str(dump),
        "endpoints": sorted({_path_of(c.url) for c in captures}),
    }

    if generate is None:
        found["generate_call"] = None
        emit(ctx, found, lambda: _table(found))
        note(
            ctx,
            "\n[yellow]No generation request was seen.[/yellow] The recording is saved; "
            f"read {dump} to see what the app did call.",
        )
        return 1

    path, body = generate
    found["generate_call"] = path
    found["model_id"] = body.get("modelId", "")
    found["model_version"] = body.get("modelVersion", "")
    found["payload_keys"] = sorted(body)

    if not ctx.args.no_save and kind == "video":
        config = ctx.config
        config = config_module.set_value(config, "video_path", path)
        config = config_module.set_value(config, "video_model_id", found["model_id"])
        config = config_module.set_value(config, "video_model_version", found["model_version"])
        found["saved_to_settings"] = True

    emit(ctx, found, lambda: _table(found))
    if found.get("saved_to_settings"):
        note(ctx, '\n[green]Recorded.[/green] Try: firefly video "a candle in the wind"')
    return 0


def _generation_call(captures: list[Any]) -> tuple[str, dict[str, Any]] | None:
    """The one POST that carried a prompt is the generation request."""
    for capture in captures:
        if capture.method.upper() != "POST" or not capture.body:
            continue
        try:
            body = json.loads(capture.body)
        except ValueError:
            continue
        if isinstance(body, dict) and body.get("prompt"):
            return _path_of(capture.url), body
    return None


def _path_of(url: str) -> str:
    return urlsplit(url).path


def _write_dump(captures: list[Any], kind: str) -> Any:
    """Save the recording with the credentials stripped.

    A capture file is a debugging artefact that gets pasted into issues, so it
    must not carry a working token out of the machine.
    """
    payload = [
        {
            "method": c.method,
            "url": c.url,
            "headers": {
                name: ("<redacted>" if name in _SECRET_HEADERS else value)
                for name, value in c.headers.items()
            },
            "body": c.body,
        }
        for c in captures
    ]
    path = capture_dir() / f"{kind}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    restrict(path)
    return path


def _table(data: dict) -> object:
    rows = []
    for key, value in data.items():
        rendered = "\n".join(str(v) for v in value) if isinstance(value, list) else str(value)
        rows.append([key, rendered])
    return build_table(
        None,
        [{"header": "Field", "style": "cyan"}, {"header": "Value", "overflow": "fold"}],
        rows,
    )
