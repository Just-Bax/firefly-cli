from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ...errors import FireflyError
from ..context import Context
from ..output import emit_payload


def register(subparsers: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    group = subparsers.add_parser(
        "raw",
        parents=[common],
        help="call a Firefly endpoint directly",
        description=(
            "An escape hatch for when Adobe moves something: send an arbitrary request "
            "with the stored session headers and print what comes back. Paths are "
            "resolved against the Firefly host; a full URL is used as given."
        ),
    )
    group.add_argument("method", help="GET, POST, ...")
    group.add_argument("target", help="a path such as /v2/storage/image, or a full URL")
    group.add_argument(
        "--data",
        metavar="JSON",
        help="request body as JSON, or @file to read it from disk",
    )
    group.set_defaults(func=cmd_raw)


def cmd_raw(ctx: Context) -> int:
    body = _body(ctx.args.data)
    response = ctx.client.call(
        ctx.args.method,
        ctx.args.target,
        json=body,
        content_type="application/json" if body is not None else "",
    )
    try:
        payload: Any = response.json()
    except ValueError:
        payload = response.text

    emit_payload(ctx, payload)
    return 0


def _body(raw: str | None) -> Any:
    if not raw:
        return None
    text = raw
    if raw.startswith("@"):
        path = Path(raw[1:]).expanduser()
        if not path.is_file():
            raise FireflyError(f"No such file: {path}")
        text = path.read_text(encoding="utf-8")

    try:
        return json.loads(text)
    except ValueError as exc:
        raise FireflyError(f"--data is not valid JSON: {exc}") from None
