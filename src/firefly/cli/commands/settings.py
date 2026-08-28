from __future__ import annotations

import argparse

from ... import config as config_module
from ...paths import browser_profile_dir, capture_dir, config_file, downloads_dir, home
from ..context import Context
from ..output import build_table, emit


def register(subparsers: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    group = subparsers.add_parser("config", parents=[common], help="show or change settings")
    actions = group.add_subparsers(dest="action")

    show = actions.add_parser("show", parents=[common], help="print the effective settings")
    show.set_defaults(func=cmd_show)

    set_cmd = actions.add_parser("set", parents=[common], help="change one setting")
    set_cmd.add_argument("key", help="setting name")
    set_cmd.add_argument("value", help="new value")
    set_cmd.set_defaults(func=cmd_set)

    group.set_defaults(func=cmd_show)


def cmd_show(ctx: Context) -> int:
    # Declared settings only, so the token sharing this file is never echoed here.
    data = ctx.config.to_dict()
    data["paths"] = {
        "home": str(home()),
        "config": str(config_file()),
        "downloads": str(ctx.config.resolved_download_dir()),
        "default_downloads": str(downloads_dir()),
        "browser_profile": str(browser_profile_dir()),
        "captures": str(capture_dir()),
    }

    def table():
        rows = [[k, str(v)] for k, v in data.items() if k != "paths"]
        rows += [[f"paths.{k}", v] for k, v in data["paths"].items()]
        return build_table(
            None,
            [{"header": "Setting", "style": "cyan"}, {"header": "Value", "overflow": "fold"}],
            rows,
        )

    emit(ctx, data, table)
    return 0


def cmd_set(ctx: Context) -> int:
    ctx.config = config_module.set_value(ctx.config, ctx.args.key, ctx.args.value)
    value = getattr(ctx.config, ctx.args.key)
    data = {"key": ctx.args.key, "value": value}

    emit(
        ctx,
        data,
        lambda: build_table(
            None,
            [{"header": "Setting", "style": "cyan"}, {"header": "Value"}],
            [[ctx.args.key, str(value)]],
        ),
    )
    return 0
