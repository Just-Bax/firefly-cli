from __future__ import annotations

import argparse

from ..context import Context
from ..output import build_table, emit


def register(subparsers: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    group = subparsers.add_parser(
        "models",
        parents=[common],
        help="the models Adobe currently offers",
        description=(
            "Read from Adobe's own discovery endpoint and cached for a day, so a model "
            "added upstream is usable without an update. Pass the Name column to --model."
        ),
    )
    group.add_argument("--kind", choices=("image", "video"), help="only one modality")
    group.add_argument(
        "--all", action="store_true", help="include models Adobe currently has disabled"
    )
    group.add_argument("--refresh", action="store_true", help="ignore the cached list")
    group.set_defaults(func=cmd_models)


def cmd_models(ctx: Context) -> int:
    found = ctx.service.models(ctx.args.kind or "", refresh=ctx.args.refresh)
    if not ctx.args.all:
        found = [m for m in found if m.enabled]
    rows = [m.to_dict() for m in found]

    def table():
        columns = [
            {"header": "Name", "style": "cyan", "overflow": "fold"},
            {"header": "Kind"},
            {"header": "Model", "overflow": "fold"},
        ]
        cells = [[r["name"], r["kind"], r["display"]] for r in rows]
        # Everything listed is enabled unless --all widened it, so the column
        # only says anything in that case.
        if ctx.args.all:
            columns.append({"header": "Enabled"})
            for cell, row in zip(cells, rows):
                cell.append("yes" if row["enabled"] else "no")
        return build_table(None, columns, cells)

    emit(ctx, rows, table, empty="Adobe returned no models.")
    return 0
